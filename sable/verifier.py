"""Deterministic verification; LLM diagnosis is only invoked after real failures."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .project import ProjectInspector
from .tools import ToolExecutor, ToolResult

CODE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".go", ".rs",
    ".c", ".h", ".cpp", ".cc", ".cs", ".php", ".rb", ".sh", ".bash",
}


@dataclass
class VerificationCheck:
    name: str
    result: ToolResult

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "result": self.result.to_dict()}


class Verifier:
    def __init__(self, executor: ToolExecutor):
        self.executor = executor

    @staticmethod
    def needs_verification(changed_files: list[str]) -> bool:
        return any(Path(path).suffix.lower() in CODE_EXTENSIONS for path in changed_files)

    def verify(self, changed_files: list[str], run_command: str | None = None) -> dict[str, Any]:
        if not self.needs_verification(changed_files) and not run_command:
            return {"status": "skipped", "summary": "No runnable code changed.", "checks": []}

        checks: list[VerificationCheck] = []
        if run_command:
            try:
                argv = shlex.split(run_command, posix=True)
            except ValueError as exc:
                result = ToolResult("verify", False, error=f"Invalid /run command: {exc}")
                checks.append(VerificationCheck("custom command", result))
            else:
                result = self.executor.run_command(argv, cwd=".")
                checks.append(VerificationCheck("custom command", result))
        else:
            inspector = ProjectInspector(self.executor.project_dir)
            for name, argv in inspector.verification_commands():
                checks.append(VerificationCheck(name, self.executor.run_command(argv, cwd=".")))

        if not checks:
            return {"status": "skipped", "summary": "No deterministic verifier was detected for this project.", "checks": []}

        failed = [check for check in checks if not check.result.success]
        if failed:
            return {
                "status": "fail",
                "summary": f"{len(failed)} of {len(checks)} verification checks failed.",
                "checks": checks,
            }
        return {
            "status": "pass",
            "summary": f"All {len(checks)} verification checks passed.",
            "checks": checks,
        }
