"""Lightweight repository intelligence and verification command discovery."""

from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path

IGNORE_DIRS = {".git", ".sable", "node_modules", "venv", ".venv", "__pycache__", "dist", "build"}

EXT_LANGUAGE = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".java": "Java",
    ".kt": "Kotlin",
    ".go": "Go",
    ".rs": "Rust",
    ".c": "C",
    ".h": "C/C++",
    ".cpp": "C++",
    ".cc": "C++",
    ".cs": "C#",
    ".php": "PHP",
    ".rb": "Ruby",
    ".sh": "Shell",
}


class ProjectInspector:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def _files(self, limit: int = 3000) -> list[Path]:
        out: list[Path] = []
        for path in self.root.rglob("*"):
            if any(part in IGNORE_DIRS for part in path.parts):
                continue
            if path.is_file():
                out.append(path)
                if len(out) >= limit:
                    break
        return out

    def profile(self) -> dict:
        files = self._files()
        counts = Counter(EXT_LANGUAGE.get(p.suffix.lower()) for p in files)
        counts.pop(None, None)
        languages = [name for name, _ in counts.most_common(5)]

        framework = ""
        package_manager = ""
        manifests: list[str] = []

        pyproject = self.root / "pyproject.toml"
        requirements = self.root / "requirements.txt"
        package_json = self.root / "package.json"

        if pyproject.exists():
            manifests.append("pyproject.toml")
            text = pyproject.read_text(errors="replace")[:20000].lower()
            for marker, name in (("fastapi", "FastAPI"), ("django", "Django"), ("flask", "Flask")):
                if marker in text:
                    framework = name
                    break
            package_manager = "Python/pip"
        elif requirements.exists():
            manifests.append("requirements.txt")
            text = requirements.read_text(errors="replace")[:10000].lower()
            for marker, name in (("fastapi", "FastAPI"), ("django", "Django"), ("flask", "Flask")):
                if marker in text:
                    framework = name
                    break
            package_manager = "Python/pip"

        if package_json.exists():
            manifests.append("package.json")
            package_manager = "npm"
            try:
                pkg = json.loads(package_json.read_text())
                deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                for marker, name in (("next", "Next.js"), ("react", "React"), ("vue", "Vue"), ("express", "Express")):
                    if marker in deps:
                        framework = name
                        break
            except (OSError, json.JSONDecodeError):
                pass

        if (self.root / "Cargo.toml").exists():
            manifests.append("Cargo.toml")
            package_manager = "cargo"
        if (self.root / "go.mod").exists():
            manifests.append("go.mod")
            package_manager = "go modules"

        return {
            "languages": languages or ["unknown"],
            "framework": framework or "unknown",
            "package_manager": package_manager or "unknown",
            "manifests": manifests,
            "file_count": len(files),
        }

    def verification_commands(self) -> list[tuple[str, list[str]]]:
        files = self._files()
        commands: list[tuple[str, list[str]]] = []

        if any(p.suffix.lower() == ".py" for p in files):
            commands.append(("Python syntax", ["python", "-m", "compileall", "-q", "."]))
            tests_dir = self.root / "tests"
            if tests_dir.is_dir():
                commands.append(("Python unit tests", ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]))
            if shutil.which("ruff") and (self.root / "pyproject.toml").exists():
                text = (self.root / "pyproject.toml").read_text(errors="replace")[:20000].lower()
                if "ruff" in text:
                    commands.append(("Ruff", ["ruff", "check", "."]))

        package_json = self.root / "package.json"
        if package_json.exists() and shutil.which("npm"):
            try:
                scripts = json.loads(package_json.read_text()).get("scripts", {})
            except (OSError, json.JSONDecodeError):
                scripts = {}
            for script, label in (("test", "npm test"), ("lint", "npm lint"), ("build", "npm build")):
                value = scripts.get(script, "")
                if value and "no test specified" not in value.lower():
                    commands.append((label, ["npm", "run", script]))

        if (self.root / "Cargo.toml").exists() and shutil.which("cargo"):
            commands.append(("cargo check", ["cargo", "check", "--quiet"]))
            commands.append(("cargo test", ["cargo", "test", "--quiet"]))

        if (self.root / "go.mod").exists() and shutil.which("go"):
            commands.append(("go test", ["go", "test", "./..."]))

        # Keep verification bounded. Heavy repos can provide an explicit /run command.
        return commands[:4]

    def render_for_prompt(self) -> str:
        p = self.profile()
        return (
            f"languages={', '.join(p['languages'])}; framework={p['framework']}; "
            f"package_manager={p['package_manager']}; manifests={p['manifests'] or 'none'}; "
            f"files≈{p['file_count']}"
        )
