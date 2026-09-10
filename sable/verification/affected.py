"""Deterministic changed-file to verification-target mapping."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..context import RepositoryContext
from .models import CheckCategory, VerificationCheck, VerificationScope


CONFIG_NAMES = {
    "pyproject.toml", "setup.cfg", "tox.ini", "pytest.ini", "mypy.ini", ".flake8",
    "ruff.toml", ".ruff.toml", "pyrightconfig.json", "pipfile", "poetry.lock", "uv.lock",
    "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "tsconfig.json",
    "cargo.toml", "cargo.lock", "go.mod", "go.sum", "go.work", "pom.xml",
    "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts",
    "mvnw", "mvnw.cmd", "gradlew", "gradlew.bat", "install.sh", "makefile",
}


def is_verification_config(path: str) -> bool:
    name = Path(path).name.lower()
    return (
        name in CONFIG_NAMES
        or name.startswith("requirements") and name.endswith(".txt")
        or name.startswith(("eslint.config.", ".eslintrc", ".prettierrc", "vite.config.", "vitest.config.", "jest.config."))
        or path.replace("\\", "/").lower().startswith(".github/workflows/") and name.endswith((".yml", ".yaml"))
    )


@dataclass(frozen=True)
class AffectedSelection:
    targets: tuple[str, ...]
    reasons: tuple[str, ...]
    central_files: tuple[str, ...] = ()
    candidates_avoided: int = 0


class AffectedTestSelector:
    def __init__(self, root: str | Path, *, max_targets: int = 20, central_fanout: int = 8):
        self.root = Path(root).resolve()
        self.max_targets = max(1, int(max_targets))
        self.central_fanout = max(2, int(central_fanout))

    @staticmethod
    def _is_test(path: str) -> bool:
        name = Path(path).name.lower()
        return (
            name.startswith("test_") or name.endswith("_test.py")
            or ".test." in name or ".spec." in name
            or "/tests/" in f"/{path.lower()}" or "/__tests__/" in f"/{path.lower()}"
        )

    def select(self, changed_files: tuple[str, ...], context: RepositoryContext) -> AffectedSelection:
        selected: dict[str, set[str]] = {}
        test_files = sorted(path for path in context.files if self._is_test(path))
        central: list[str] = []

        def add(target: str, reason: str) -> None:
            if target in test_files or self._is_test(target):
                selected.setdefault(target, set()).add(reason)

        for changed in changed_files:
            if self._is_test(changed):
                add(changed, "changed test file")
            for target in context.test_relationships.get(changed, []):
                add(target, f"Context Engine relationship from {changed}")
            importers = context.importers.get(changed, [])
            is_central = len(importers) >= self.central_fanout
            if is_central:
                central.append(changed)
            frontier = list(importers[:100])
            seen = set(frontier)
            for depth in range(2 if is_central else 1):
                next_frontier: list[str] = []
                for importer in frontier:
                    if self._is_test(importer):
                        add(importer, f"reverse import from {changed}")
                    importer_stem = Path(importer).stem.lower()
                    for target in context.test_relationships.get(importer, []):
                        if importer_stem in Path(target).stem.lower():
                            add(target, f"test for reverse importer {importer}")
                    if depth == 0:
                        for parent in context.importers.get(importer, [])[:50]:
                            if parent not in seen:
                                seen.add(parent)
                                next_frontier.append(parent)
                frontier = next_frontier

            stem = Path(changed).stem.lower()
            for test in test_files[:500]:
                name = Path(test).name.lower()
                if name in {f"test_{stem}.py", f"{stem}_test.py"} or name.startswith(f"{stem}.test.") or name.startswith(f"{stem}.spec."):
                    add(test, f"filename match for {changed}")

        targets = tuple(sorted(selected)[:self.max_targets])
        reasons = tuple(
            f"{target}: {'; '.join(sorted(selected[target]))}"
            for target in targets
        )
        return AffectedSelection(
            targets,
            reasons,
            tuple(sorted(central)),
            max(0, len(test_files) - len(targets)),
        )

    @staticmethod
    def _relative_target(target: str, cwd: str) -> str:
        prefix = "" if cwd == "." else cwd.rstrip("/") + "/"
        return target[len(prefix):] if prefix and target.startswith(prefix) else target

    def refine_checks(
        self,
        checks: list[VerificationCheck],
        changed_files: tuple[str, ...],
        context: RepositoryContext,
        scope: VerificationScope,
    ) -> tuple[list[VerificationCheck], AffectedSelection]:
        selection = self.select(changed_files, context)
        if scope == VerificationScope.FULL:
            return checks, selection
        refined: list[VerificationCheck] = []
        for check in checks:
            targets = tuple(
                target for target in selection.targets
                if check.cwd == "." or target.startswith(check.cwd.rstrip("/") + "/")
            )
            relative_targets = tuple(self._relative_target(target, check.cwd) for target in targets)
            if check.category == CheckCategory.SYNTAX and check.language == "Python":
                python_files = tuple(
                    self._relative_target(path, check.cwd)
                    for path in changed_files
                    if path.endswith(".py")
                    and (check.cwd == "." or path.startswith(check.cwd.rstrip("/") + "/"))
                    and (self.root / path).is_file()
                )[:100]
                if python_files:
                    refined.append(check.with_updates(
                        argv=("python", "-m", "py_compile", *python_files),
                        reason="Targeted syntax validation for changed Python files.",
                    ))
                    continue
            if check.category == CheckCategory.UNIT_TEST and relative_targets:
                if check.language == "Python" and check.name == "pytest":
                    refined.append(check.with_updates(
                        argv=("pytest", "-q", *relative_targets),
                        reason="Affected pytest targets selected deterministically by the Context Engine.",
                        target_reasons=tuple(reason for reason in selection.reasons if reason.split(":", 1)[0] in targets),
                    ))
                    continue
                if check.language == "Python" and check.name == "Python unit tests":
                    refined.append(check.with_updates(
                        argv=("python", "-m", "unittest", *relative_targets),
                        reason="Affected unittest targets selected deterministically by the Context Engine.",
                        target_reasons=tuple(reason for reason in selection.reasons if reason.split(":", 1)[0] in targets),
                    ))
                    continue
                if check.name in {"Vitest", "Jest"}:
                    refined.append(check.with_updates(
                        argv=(*check.argv, *relative_targets),
                        reason=f"Affected {check.name} targets selected by bounded filename/import heuristics.",
                        target_reasons=tuple(reason for reason in selection.reasons if reason.split(":", 1)[0] in targets),
                    ))
                    continue
            refined.append(check)
        return refined, selection
