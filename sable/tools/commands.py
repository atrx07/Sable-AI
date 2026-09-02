"""Process execution tools."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from ..security import WorkspaceViolation, sanitized_environment
from .base import ToolResult


class CommandMixin:
    def _validate_command_paths(self, argv: list[str], cwd: str) -> ToolResult | None:
        """Reject command arguments that resolve outside the workspace, including symlink escapes."""
        try:
            base = self._resolve(cwd)
        except WorkspaceViolation as exc:
            return ToolResult("run_command", False, error=str(exc), risk="blocked")
        for token in argv[1:]:
            if not isinstance(token, str) or not token or token.startswith("-"):
                continue
            candidate = Path(token).expanduser()
            looks_like_path = candidate.is_absolute() or "/" in token or "\\" in token or (base / candidate).exists()
            if not looks_like_path:
                continue
            try:
                self.workspace.resolve(token, base=base)
            except WorkspaceViolation as exc:
                return ToolResult("run_command", False, error=str(exc), risk="blocked")
        return None

    def run_command(
        self,
        argv: list[str],
        cwd: str = ".",
        timeout: int | None = None,
        *,
        sanitize_env: bool = True,
    ) -> ToolResult:
        denied = self._validate_command_paths(argv, cwd)
        if denied:
            return denied
        env = sanitized_environment() if sanitize_env else None
        return self._run(argv, cwd=cwd, timeout=timeout, tool="run_command", env=env)

    def run_shell(self, command: str, cwd: str = ".", timeout: int | None = None) -> ToolResult:
        try:
            target_cwd = self._resolve(cwd)
        except (WorkspaceViolation, OSError) as exc:
            return ToolResult("run_shell", False, error=str(exc), risk="high")
        started = time.monotonic()
        try:
            proc = subprocess.run(
                command,
                cwd=str(target_cwd),
                capture_output=True,
                text=True,
                timeout=int(timeout or self.command_timeout),
                shell=True,
            )
            duration = int((time.monotonic() - started) * 1000)
            text, truncated = self._trim(((proc.stdout or "") + (proc.stderr or "")).strip())
            if proc.returncode == 0:
                return ToolResult("run_shell", True, output=text, exit_code=0, duration_ms=duration, truncated=truncated, risk="high")
            return ToolResult("run_shell", False, error=text or f"Exited with code {proc.returncode}", exit_code=proc.returncode, duration_ms=duration, truncated=truncated, risk="high")
        except subprocess.TimeoutExpired:
            return ToolResult("run_shell", False, error=f"Command timed out after {int(timeout or self.command_timeout)}s", risk="high")
        except OSError as exc:
            return ToolResult("run_shell", False, error=str(exc), risk="high")
