"""Process execution tools."""

from __future__ import annotations

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
            if not isinstance(token, str) or not token:
                continue
            candidates = [token]
            if "=" in token:
                candidates.append(token.split("=", 1)[1])
            for prefix in ("-f", "-o", "-C", "-S", "-B", "-I", "-L"):
                if token.startswith(prefix) and len(token) > len(prefix):
                    candidates.append(token[len(prefix):])
            for raw in candidates:
                if raw.startswith("-"):
                    continue
                candidate = Path(raw).expanduser()
                looks_like_path = candidate.is_absolute() or "/" in raw or "\\" in raw or (base / candidate).exists()
                if not looks_like_path:
                    continue
                try:
                    self.workspace.resolve(raw, base=base)
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
        result = self._run(
            command,
            cwd=cwd,
            timeout=timeout,
            tool="run_shell",
            shell=True,
        )
        result.risk = "high"
        return result
