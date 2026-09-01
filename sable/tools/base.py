"""Shared tool result and executor primitives."""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import is_blocked_path, redact_secrets
from ..project import ProjectInspector
from ..security import Workspace, WorkspaceViolation

MAX_OUTPUT_CHARS = 12000


@dataclass
class ToolResult:
    tool: str
    success: bool
    output: str = ""
    error: str = ""
    exit_code: int | None = None
    duration_ms: int = 0
    changed_files: list[str] = field(default_factory=list)
    truncated: bool = False
    approval_required: bool = False
    risk: str = "normal"

    def __post_init__(self):
        self.output = str(self.output or "")
        self.error = str(self.error or "")

    def __str__(self) -> str:
        body = self.output if self.success else self.error
        return f"[{self.tool}] {'OK' if self.success else 'ERROR'}\n{body}".strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "changed_files": list(self.changed_files),
            "truncated": self.truncated,
            "approval_required": self.approval_required,
            "risk": self.risk,
        }


class ToolCore:
    def __init__(self, project_dir: str, command_timeout: int = 120):
        self.workspace = Workspace(project_dir)
        self.project_dir = str(self.workspace.root)
        self.command_timeout = int(command_timeout)

    @property
    def current_dir(self) -> str:
        return str(self.workspace.cwd)

    def _resolve(self, path: str = ".") -> Path:
        return self.workspace.resolve(path)

    def _rel(self, path: Path) -> str:
        return self.workspace.relative(path)

    @staticmethod
    def _trim(text: str) -> tuple[str, bool]:
        text = redact_secrets(text)
        if len(text) <= MAX_OUTPUT_CHARS:
            return text, False
        half = MAX_OUTPUT_CHARS // 2
        omitted = len(text) - MAX_OUTPUT_CHARS
        return text[:half] + f"\n... [{omitted} chars omitted] ...\n" + text[-half:], True

    def _run(
        self,
        argv: list[str],
        *,
        cwd: str | Path | None = None,
        timeout: int | None = None,
        tool: str = "run_command",
        env: dict[str, str] | None = None,
    ) -> ToolResult:
        try:
            target_cwd = self._resolve(str(cwd)) if cwd else self.workspace.cwd
        except (WorkspaceViolation, OSError) as exc:
            return ToolResult(tool, False, error=str(exc))

        started = time.monotonic()
        try:
            proc = subprocess.run(
                argv,
                cwd=str(target_cwd),
                capture_output=True,
                text=True,
                timeout=int(timeout or self.command_timeout),
                shell=False,
                env=env,
            )
            duration = int((time.monotonic() - started) * 1000)
            combined = (proc.stdout or "") + (proc.stderr or "")
            combined, truncated = self._trim(combined.strip())
            if proc.returncode == 0:
                return ToolResult(tool, True, output=combined, exit_code=0, duration_ms=duration, truncated=truncated)
            return ToolResult(tool, False, error=combined or f"Exited with code {proc.returncode}", exit_code=proc.returncode, duration_ms=duration, truncated=truncated)
        except subprocess.TimeoutExpired as exc:
            duration = int((time.monotonic() - started) * 1000)
            out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            err = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
            text, truncated = self._trim((out + err).strip())
            suffix = f"Command timed out after {int(timeout or self.command_timeout)}s"
            return ToolResult(tool, False, error=(text + "\n" + suffix).strip(), duration_ms=duration, truncated=truncated)
        except (OSError, ValueError) as exc:
            duration = int((time.monotonic() - started) * 1000)
            return ToolResult(tool, False, error=str(exc), duration_ms=duration)

    def _safe_path(self, path: str, tool: str) -> tuple[Path | None, ToolResult | None]:
        if is_blocked_path(path):
            return None, ToolResult(tool, False, error=f"Access denied: '{path}' is a protected path.")
        try:
            target = self._resolve(path)
            resolved_rel = self._rel(target)
            if is_blocked_path(resolved_rel):
                return None, ToolResult(tool, False, error=f"Access denied: '{resolved_rel}' is a protected path.")
            return target, None
        except WorkspaceViolation as exc:
            return None, ToolResult(tool, False, error=str(exc), risk="blocked")

    def project_profile(self) -> ToolResult:
        inspector = ProjectInspector(self.project_dir)
        return ToolResult("project_profile", True, output=json.dumps(inspector.profile(), indent=2))
