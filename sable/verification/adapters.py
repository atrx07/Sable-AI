"""Manifest-first, side-effect-free verification adapters."""

from __future__ import annotations

import json
import os
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, Iterable

from .models import (
    CheckAvailability,
    CheckCategory,
    VerificationCheck,
    VerificationScope,
)


Finder = Callable[[str], str | None]


class AvailabilityResolver:
    """Resolve tools with bounded PATH and local-project filesystem checks."""

    def __init__(self, repository_root: Path, finder: Finder = shutil.which):
        self.repository_root = repository_root.resolve()
        self.finder = finder

    def executable(self, executable: str, cwd: str = ".") -> tuple[CheckAvailability, str]:
        candidate = Path(executable)
        if candidate.is_absolute() or "/" in executable or "\\" in executable:
            base = self.repository_root / cwd
            local = candidate if candidate.is_absolute() else base / candidate
            variants = [local]
            if os.name == "nt" and not local.suffix:
                variants.extend((local.with_suffix(".cmd"), local.with_suffix(".bat"), local.with_suffix(".exe")))
            for path in variants:
                try:
                    resolved = path.resolve(strict=True)
                    resolved.relative_to(self.repository_root)
                except (OSError, ValueError):
                    continue
                if resolved.is_file():
                    return CheckAvailability.AVAILABLE, "Local project executable exists inside the workspace."
            return CheckAvailability.UNAVAILABLE, f"Configured executable is missing: {executable}"
        if self.finder(executable):
            return CheckAvailability.AVAILABLE, "Executable found on PATH."
        return CheckAvailability.UNAVAILABLE, f"Configured executable is unavailable on PATH: {executable}"

    def project_tool(self, executable: str, cwd: str = ".") -> tuple[str, CheckAvailability, str]:
        bin_root = self.repository_root / cwd / "node_modules" / ".bin"
        variants = [bin_root / executable]
        if os.name == "nt":
            variants.extend((bin_root / f"{executable}.cmd", bin_root / f"{executable}.exe"))
        for path in variants:
            try:
                resolved = path.resolve(strict=True)
                resolved.relative_to(self.repository_root)
            except (OSError, ValueError):
                continue
            if resolved.is_file():
                relative = path.relative_to(self.repository_root / cwd).as_posix()
                return relative, CheckAvailability.AVAILABLE, "Local project executable exists."
        availability, reason = self.executable(executable, cwd)
        return executable, availability, reason


class VerificationAdapter(ABC):
    language = "unknown"
    primary_manifests: tuple[str, ...] = ()

    def __init__(self, repository_root: Path, project_root: Path, resolver: AvailabilityResolver):
        self.repository_root = repository_root.resolve()
        self.project_root = project_root.resolve()
        self.resolver = resolver
        self.cwd = self.project_root.relative_to(self.repository_root).as_posix() or "."
        self.warnings: list[str] = []

    def exists(self, name: str) -> bool:
        try:
            path = (self.project_root / name).resolve(strict=True)
            path.relative_to(self.repository_root)
            return path.exists()
        except (OSError, ValueError):
            return False

    def read_text(self, name: str, limit: int = 100_000) -> str:
        try:
            path = (self.project_root / name).resolve(strict=True)
            path.relative_to(self.repository_root)
            return path.read_text(encoding="utf-8", errors="replace")[:limit]
        except (OSError, ValueError) as exc:
            self.warnings.append(f"Could not inspect {self._display(name)}: {exc}")
            return ""

    def _display(self, name: str) -> str:
        prefix = "" if self.cwd == "." else f"{self.cwd}/"
        return prefix + name

    def affected(self, changed_files: Iterable[str]) -> tuple[str, ...]:
        prefix = "" if self.cwd == "." else self.cwd.rstrip("/") + "/"
        return tuple(path for path in changed_files if not prefix or path == self.cwd or path.startswith(prefix))[:100]

    def check(
        self,
        name: str,
        category: CheckCategory,
        argv: list[str],
        changed_files: Iterable[str],
        *,
        scope: VerificationScope,
        toolchain: str | None = None,
        required: bool = True,
        availability: tuple[CheckAvailability, str] | None = None,
        reason: str,
        dependencies: tuple[str, ...] = (),
    ) -> VerificationCheck:
        state, availability_reason = availability or self.resolver.executable(argv[0], self.cwd)
        return VerificationCheck.create(
            name,
            category,
            argv,
            cwd=self.cwd,
            language=self.language,
            toolchain=toolchain or argv[0],
            scope=scope,
            reason=reason,
            affected_files=self.affected(changed_files),
            required=required,
            availability=state,
            availability_reason=availability_reason,
            dependencies=dependencies,
        )

    @abstractmethod
    def discover(self, changed_files: tuple[str, ...]) -> list[VerificationCheck]:
        raise NotImplementedError


class PythonAdapter(VerificationAdapter):
    language = "Python"
    primary_manifests = (
        "pyproject.toml", "setup.cfg", "tox.ini", "pytest.ini", "mypy.ini",
        ".flake8", "ruff.toml", ".ruff.toml", "pyrightconfig.json",
        "Pipfile", "poetry.lock", "uv.lock", "requirements.txt", "requirements-dev.txt",
    )

    def _has_python(self) -> bool:
        if any(self.exists(name) for name in self.primary_manifests):
            return True
        try:
            if any(self.project_root.glob("requirements*.txt")):
                return True
            visited = 0
            for current, dirnames, filenames in os.walk(self.project_root):
                depth = len(Path(current).relative_to(self.project_root).parts)
                dirnames[:] = [
                    name for name in dirnames
                    if name not in {".git", ".venv", "venv", "node_modules", "build", "dist"} and depth < 5
                ]
                visited += len(filenames)
                if any(name.endswith(".py") for name in filenames):
                    return True
                if visited >= 1000:
                    break
        except OSError:
            pass
        return False

    def discover(self, changed_files: tuple[str, ...]) -> list[VerificationCheck]:
        if not self._has_python():
            return []
        checks: list[VerificationCheck] = []
        pyproject = self.read_text("pyproject.toml") if self.exists("pyproject.toml") else ""
        if pyproject and (pyproject.count("[") != pyproject.count("]") or pyproject.count('"') % 2):
            self.warnings.append(f"Malformed TOML may limit discovery: {self._display('pyproject.toml')}")

        checks.append(self.check(
            "Python syntax", CheckCategory.SYNTAX,
            ["python", "-m", "compileall", "-q", "."], changed_files,
            scope=VerificationScope.QUICK,
            reason="Python sources or project configuration were detected.",
        ))

        setup_cfg = self.read_text("setup.cfg") if self.exists("setup.cfg") else ""
        tox_ini = self.read_text("tox.ini") if self.exists("tox.ini") else ""
        lower = (pyproject + "\n" + setup_cfg + "\n" + tox_ini).lower()
        pytest_configured = (
            self.exists("pytest.ini") or "[tool.pytest" in lower or "[tool:pytest]" in lower or "pytest" in tox_ini.lower()
        )
        tests_dir = self.project_root / "tests"
        if pytest_configured:
            checks.append(self.check(
                "pytest", CheckCategory.UNIT_TEST, ["pytest", "-q"], changed_files,
                scope=VerificationScope.AFFECTED,
                reason="Repository configuration selects pytest.",
            ))
        elif tests_dir.is_dir():
            checks.append(self.check(
                "Python unit tests", CheckCategory.UNIT_TEST,
                ["python", "-m", "unittest", "discover", "-s", "tests", "-v"], changed_files,
                scope=VerificationScope.AFFECTED,
                reason="A conventional Python unittest directory was detected.",
            ))

        configured = (
            ("Ruff", CheckCategory.LINT, "ruff", ["ruff", "check", "."], self.exists("ruff.toml") or self.exists(".ruff.toml") or "[tool.ruff" in lower),
            ("Flake8", CheckCategory.LINT, "flake8", ["flake8", "."], self.exists(".flake8") or "[flake8]" in lower),
            ("Black check", CheckCategory.FORMAT, "black", ["black", "--check", "."], "[tool.black" in lower),
            ("MyPy", CheckCategory.TYPECHECK, "mypy", ["mypy", "."], self.exists("mypy.ini") or "[tool.mypy" in lower or "[mypy]" in lower),
            ("Pyright", CheckCategory.TYPECHECK, "pyright", ["pyright"], self.exists("pyrightconfig.json") or "[tool.pyright" in lower),
        )
        for name, category, tool, argv, enabled in configured:
            if enabled:
                checks.append(self.check(
                    name, category, argv, changed_files,
                    scope=VerificationScope.QUICK if category in {CheckCategory.LINT, CheckCategory.FORMAT} else VerificationScope.AFFECTED,
                    toolchain=tool,
                    required=False,
                    reason=f"{tool} is explicitly configured by the repository.",
                ))
        return checks


class NodeAdapter(VerificationAdapter):
    language = "JavaScript/TypeScript"
    primary_manifests = (
        "package.json", "tsconfig.json", "eslint.config.js", "eslint.config.mjs",
        ".eslintrc", ".eslintrc.json", ".prettierrc", "prettier.config.js",
        "vite.config.js", "vite.config.ts", "vitest.config.js", "vitest.config.ts",
        "jest.config.js", "jest.config.ts",
    )
    unsafe_scripts = {"prepublish", "prepublishonly", "postinstall", "deploy", "release", "publish"}

    def _package_manager(self) -> str:
        if self.exists("pnpm-lock.yaml"):
            return "pnpm"
        if self.exists("yarn.lock"):
            return "yarn"
        return "npm"

    def _script_availability(self, manager: str, package: dict) -> tuple[CheckAvailability, str]:
        available, reason = self.resolver.executable(manager, self.cwd)
        dependencies = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
        if available == CheckAvailability.AVAILABLE and dependencies and not self.exists("node_modules"):
            return CheckAvailability.UNAVAILABLE, "Package dependencies are not installed; verification will not auto-install them."
        return available, reason

    def discover(self, changed_files: tuple[str, ...]) -> list[VerificationCheck]:
        if not any(self.exists(name) for name in self.primary_manifests):
            return []
        package: dict = {}
        if self.exists("package.json"):
            try:
                package = json.loads(self.read_text("package.json"))
                if not isinstance(package, dict):
                    raise ValueError("top-level value is not an object")
            except (json.JSONDecodeError, ValueError) as exc:
                self.warnings.append(f"Malformed package.json at {self._display('package.json')}: {exc}")
                package = {}
        scripts = package.get("scripts", {}) if isinstance(package.get("scripts", {}), dict) else {}
        manager = self._package_manager()
        manager_state = self._script_availability(manager, package)
        checks: list[VerificationCheck] = []
        known_scripts = (
            ("lint", CheckCategory.LINT, VerificationScope.QUICK),
            ("format:check", CheckCategory.FORMAT, VerificationScope.QUICK),
            ("typecheck", CheckCategory.TYPECHECK, VerificationScope.AFFECTED),
            ("test", CheckCategory.UNIT_TEST, VerificationScope.AFFECTED),
            ("check", CheckCategory.STATIC_ANALYSIS, VerificationScope.FULL),
            ("build", CheckCategory.BUILD, VerificationScope.FULL),
        )
        selected_scripts: set[str] = set()
        for script, category, scope in known_scripts:
            value = scripts.get(script)
            if not isinstance(value, str) or not value.strip() or "no test specified" in value.lower():
                continue
            if script in self.unsafe_scripts:
                continue
            argv = [manager, "run", script]
            checks.append(self.check(
                f"{manager} {script}", category, argv, changed_files,
                scope=scope,
                toolchain=manager,
                availability=manager_state,
                reason=f"Known-safe package.json verification script '{script}' is configured.",
                dependencies=("existing node_modules when package dependencies are declared",),
            ))
            selected_scripts.add(script)

        config_names = {path.name.lower() for path in self.project_root.iterdir() if path.is_file()}
        dependencies = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
        direct: list[tuple[str, CheckCategory, str, list[str], VerificationScope, bool]] = [
            ("TypeScript", CheckCategory.TYPECHECK, "tsc", ["tsc", "--noEmit"], VerificationScope.AFFECTED, self.exists("tsconfig.json") and "typecheck" not in selected_scripts),
            ("ESLint", CheckCategory.LINT, "eslint", ["eslint", "."], VerificationScope.QUICK, "lint" not in selected_scripts and any(name.startswith("eslint.config.") or name.startswith(".eslintrc") for name in config_names)),
            ("Prettier check", CheckCategory.FORMAT, "prettier", ["prettier", "--check", "."], VerificationScope.QUICK, "format:check" not in selected_scripts and any(name.startswith(".prettierrc") or name == "prettier.config.js" for name in config_names)),
            ("Vitest", CheckCategory.UNIT_TEST, "vitest", ["vitest", "run"], VerificationScope.AFFECTED, "test" not in selected_scripts and ("vitest" in dependencies or any(name.startswith("vitest.config.") for name in config_names))),
            ("Jest", CheckCategory.UNIT_TEST, "jest", ["jest", "--runInBand"], VerificationScope.AFFECTED, "test" not in selected_scripts and ("jest" in dependencies or any(name.startswith("jest.config.") for name in config_names))),
        ]
        for name, category, tool, argv, scope, enabled in direct:
            if not enabled:
                continue
            executable, state, reason = self.resolver.project_tool(tool, self.cwd)
            if dependencies and not self.exists("node_modules"):
                state = CheckAvailability.UNAVAILABLE
                reason = "Package dependencies are not installed; verification will not auto-install them."
            argv[0] = executable
            checks.append(self.check(
                name, category, argv, changed_files,
                scope=scope,
                toolchain=tool,
                required=category in {CheckCategory.TYPECHECK, CheckCategory.UNIT_TEST},
                availability=(state, reason),
                reason=f"{tool} configuration was detected without an equivalent package script.",
                dependencies=("existing local JavaScript dependencies",),
            ))
        return checks


class RustAdapter(VerificationAdapter):
    language = "Rust"
    primary_manifests = ("Cargo.toml",)

    def discover(self, changed_files: tuple[str, ...]) -> list[VerificationCheck]:
        if not self.exists("Cargo.toml"):
            return []
        cargo = self.resolver.executable("cargo", self.cwd)
        checks = [
            self.check("cargo check", CheckCategory.BUILD, ["cargo", "check", "--quiet", "--offline"], changed_files, scope=VerificationScope.QUICK, availability=cargo, reason="Cargo.toml defines a Rust package or workspace.", dependencies=("locally cached or vendored crates",)),
            self.check("cargo test", CheckCategory.UNIT_TEST, ["cargo", "test", "--quiet", "--offline"], changed_files, scope=VerificationScope.AFFECTED, availability=cargo, reason="Cargo.toml defines deterministic Rust tests.", dependencies=("locally cached or vendored crates",)),
        ]
        rustfmt = self.resolver.executable("rustfmt", self.cwd)
        clippy = self.resolver.executable("cargo-clippy", self.cwd)
        checks.extend((
            self.check("cargo fmt", CheckCategory.FORMAT, ["cargo", "fmt", "--check"], changed_files, scope=VerificationScope.QUICK, required=False, availability=rustfmt, reason="Rust formatting validation is supported for Cargo projects."),
            self.check("cargo clippy", CheckCategory.LINT, ["cargo", "clippy", "--quiet", "--offline", "--", "-D", "warnings"], changed_files, scope=VerificationScope.FULL, required=False, availability=clippy, reason="Clippy static analysis is supported when installed.", dependencies=("locally cached or vendored crates",)),
        ))
        return checks


class GoAdapter(VerificationAdapter):
    language = "Go"
    primary_manifests = ("go.mod", "go.work")

    def discover(self, changed_files: tuple[str, ...]) -> list[VerificationCheck]:
        if not (self.exists("go.mod") or self.exists("go.work")):
            return []
        go = self.resolver.executable("go", self.cwd)
        return [
            self.check("go test", CheckCategory.UNIT_TEST, ["go", "test", "-mod=readonly", "./..."], changed_files, scope=VerificationScope.AFFECTED, availability=go, reason="A Go module/workspace manifest was detected.", dependencies=("declared modules without modifying go.mod/go.sum",)),
            self.check("go vet", CheckCategory.STATIC_ANALYSIS, ["go", "vet", "-mod=readonly", "./..."], changed_files, scope=VerificationScope.AFFECTED, availability=go, reason="A Go module/workspace manifest was detected.", dependencies=("declared modules without modifying go.mod/go.sum",)),
        ]


class JavaAdapter(VerificationAdapter):
    language = "Java/JVM"
    primary_manifests = ("pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts")

    def _wrapper(self, stem: str, system: str) -> tuple[str, tuple[CheckAvailability, str]]:
        variants = ([f"{stem}.cmd", f"{stem}.bat", stem] if os.name == "nt" else [stem, f"{stem}.cmd", f"{stem}.bat"])
        for name in variants:
            command = f"./{name}"
            state = self.resolver.executable(command, self.cwd)
            if state[0] == CheckAvailability.AVAILABLE:
                return command, state
        return system, self.resolver.executable(system, self.cwd)

    def discover(self, changed_files: tuple[str, ...]) -> list[VerificationCheck]:
        checks: list[VerificationCheck] = []
        if self.exists("pom.xml"):
            executable, state = self._wrapper("mvnw", "mvn")
            checks.extend((
                self.check("Maven test", CheckCategory.UNIT_TEST, [executable, "--batch-mode", "--offline", "test"], changed_files, scope=VerificationScope.AFFECTED, toolchain="maven", availability=state, reason="pom.xml defines a Maven project; repository wrapper is preferred.", dependencies=("locally cached Maven dependencies",)),
                self.check("Maven verify", CheckCategory.BUILD, [executable, "--batch-mode", "--offline", "verify"], changed_files, scope=VerificationScope.FULL, toolchain="maven", availability=state, reason="pom.xml defines Maven's full verification lifecycle.", dependencies=("locally cached Maven dependencies",)),
            ))
        if self.exists("build.gradle") or self.exists("build.gradle.kts"):
            executable, state = self._wrapper("gradlew", "gradle")
            checks.extend((
                self.check("Gradle test", CheckCategory.UNIT_TEST, [executable, "--offline", "test"], changed_files, scope=VerificationScope.AFFECTED, toolchain="gradle", availability=state, reason="A Gradle build manifest was detected; repository wrapper is preferred.", dependencies=("locally cached Gradle dependencies",)),
                self.check("Gradle check", CheckCategory.BUILD, [executable, "--offline", "check"], changed_files, scope=VerificationScope.FULL, toolchain="gradle", availability=state, reason="Gradle's configured verification lifecycle is available.", dependencies=("locally cached Gradle dependencies",)),
            ))
        return checks


DEFAULT_ADAPTERS = (PythonAdapter, NodeAdapter, RustAdapter, GoAdapter, JavaAdapter)
