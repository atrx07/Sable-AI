"""Git tools using ambient Git authentication; Sable stores no PATs."""

from __future__ import annotations

import shlex
from pathlib import Path

from ..config import contains_secret, redact_secrets
from ..security import WorkspaceViolation
from .base import ToolResult


class GitMixin:
    def _git(self, args: list[str], tool: str) -> ToolResult:
        return self._run(["git", *args], cwd=self.project_dir, tool=tool)
    def git_init(self, remote: str | None = None) -> ToolResult:
        if (Path(self.project_dir) / ".git").exists():
            result = ToolResult("git_init", True, output="Git repository already initialized.")
        else:
            result = self._git(["init", "-b", "main"], "git_init")
            if not result.success:
                # Older git versions may not support -b.
                result = self._git(["init"], "git_init")
                if result.success:
                    self._git(["branch", "-M", "main"], "git_init")
        if result.success and remote:
            remote_result = self.git_set_remote(remote)
            if not remote_result.success:
                return remote_result
        return result
    @staticmethod
    def _validate_git_remote_arg(value: str, tool: str) -> ToolResult | None:
        value = str(value or "").strip()
        if not value:
            return ToolResult(tool, False, error="Git remote URL cannot be empty.", risk="high")
        if value.startswith("-"):
            return ToolResult(tool, False, error="Git remote URL cannot start with '-'.", risk="blocked")
        if contains_secret(value):
            return ToolResult(tool, False, error="Refused: remote URL appears to contain embedded credentials.", risk="blocked")
        return None
    def _validate_git_branch(self, branch: str, tool: str) -> ToolResult | None:
        branch = str(branch or "").strip()
        if not branch:
            return ToolResult(tool, False, error="Git branch cannot be empty.")
        if branch.startswith("-"):
            return ToolResult(tool, False, error="Git branch cannot start with '-'.", risk="blocked")
        check = self._git(["check-ref-format", "--branch", branch], tool)
        if not check.success:
            return ToolResult(tool, False, error=f"Invalid Git branch name: {branch}", risk="blocked")
        return None
    def git_set_remote(self, url: str) -> ToolResult:
        denied = self._validate_git_remote_arg(url, "git_set_remote")
        if denied:
            return denied
        url = str(url).strip()
        existing = self._git(["remote", "get-url", "origin"], "git_set_remote")
        if existing.success:
            result = self._git(["remote", "set-url", "origin", url], "git_set_remote")
        else:
            result = self._git(["remote", "add", "origin", url], "git_set_remote")
        if result.success:
            result.output = f"Remote origin set to: {url}"
        return result
    def git_add(self, files: str = ".") -> ToolResult:
        try:
            parts = shlex.split(files) or ["."]
        except ValueError as exc:
            return ToolResult("git_add", False, error=str(exc))
        return self._git(["add", "--", *parts], "git_add")
    def git_add_paths(self, paths: list[str]) -> ToolResult:
        safe: list[str] = []
        for path in paths:
            try:
                target = self._resolve(path)
                safe.append(self._rel(target))
            except WorkspaceViolation as exc:
                return ToolResult("git_add", False, error=str(exc))
        if not safe:
            return ToolResult("git_add", True, output="Nothing to stage.")
        return self._git(["add", "-A", "--", *sorted(set(safe))], "git_add")
    def git_commit(self, message: str) -> ToolResult:
        if contains_secret(message):
            return ToolResult("git_commit", False, error="Commit aborted: message appears to contain a secret.")
        diff = self._git(["diff", "--cached", "--no-ext-diff"], "git_commit")
        if diff.success and contains_secret(diff.output):
            return ToolResult("git_commit", False, error="Commit aborted: staged diff contains a likely secret.")
        result = self._git(["commit", "-m", message], "git_commit")
        if not result.success and ("nothing to commit" in result.error.lower() or "nothing added" in result.error.lower()):
            return ToolResult("git_commit", True, output="Nothing new to commit.")
        if result.success:
            latest = self._git(["log", "--oneline", "-1"], "git_commit")
            result.output = f"Committed: {latest.output.strip()}" if latest.success else result.output
        return result
    def current_branch(self) -> str:
        result = self._git(["branch", "--show-current"], "git_branch")
        return result.output.strip() if result.success and result.output.strip() else "main"
    def git_push(self, branch: str = "") -> ToolResult:
        branch = branch or self.current_branch()
        denied = self._validate_git_branch(branch, "git_push")
        if denied:
            denied.risk = "high"
            return denied
        remote = self._git(["remote", "get-url", "origin"], "git_push")
        if not remote.success:
            return ToolResult("git_push", False, error="__NO_REMOTE__", risk="high")
        result = self._git(["push", "-u", "origin", branch], "git_push")
        result.risk = "high"
        return result
    def git_pull(self, branch: str = "") -> ToolResult:
        branch = branch or self.current_branch()
        denied = self._validate_git_branch(branch, "git_pull")
        if denied:
            denied.risk = "high"
            return denied
        result = self._git(["pull", "--rebase", "origin", branch], "git_pull")
        result.risk = "high"
        return result
    def git_status(self) -> ToolResult:
        return self._git(["status", "--short"], "git_status")
    def git_log(self, n: int = 10) -> ToolResult:
        return self._git(["log", "--oneline", "--decorate", "--graph", "-n", str(max(1, min(50, int(n))))], "git_log")
    def git_diff(self, file: str = "") -> ToolResult:
        args = ["diff", "--no-ext-diff"]
        if file:
            try:
                target = self._resolve(file)
                args += ["--", self._rel(target)]
            except WorkspaceViolation as exc:
                return ToolResult("git_diff", False, error=str(exc))
        result = self._git(args, "git_diff")
        result.output = redact_secrets(result.output)
        return result
    def git_clone(self, url: str, dest: str = "") -> ToolResult:
        denied = self._validate_git_remote_arg(url, "git_clone")
        if denied:
            return denied
        url = str(url).strip()
        if dest:
            try:
                target = self._resolve(dest)
            except WorkspaceViolation as exc:
                return ToolResult("git_clone", False, error=str(exc), risk="high")
            args = ["clone", url, str(target)]
        else:
            args = ["clone", url]
        result = self._git(args, "git_clone")
        result.risk = "high"
        return result
    def git_branch(self, name: str = "") -> ToolResult:
        if not name:
            return self._git(["branch", "-a"], "git_branch")
        name = str(name).strip()
        denied = self._validate_git_branch(name, "git_branch")
        if denied:
            return denied
        exists = self._git(["show-ref", "--verify", f"refs/heads/{name}"], "git_branch")
        if exists.success:
            return self._git(["switch", name], "git_branch")
        result = self._git(["switch", "-c", name], "git_branch")
        if not result.success:
            # Termux can have older git versions.
            result = self._git(["checkout", "-b", name], "git_branch")
        return result
    def git_stash(self, action: str = "push") -> ToolResult:
        if action in {"pop", "apply"}:
            result = self._git(["stash", action], "git_stash")
        else:
            result = self._git(["stash", "push", "-m", "sable stash"], "git_stash")
        result.risk = "high"
        return result
    def git_ahead_count(self, branch: str = "") -> int:
        branch = branch or self.current_branch()
        if self._validate_git_branch(branch, "git_status"):
            return 0
        result = self._git(["rev-list", "--count", f"origin/{branch}..HEAD"], "git_status")
        try:
            return int(result.output.strip()) if result.success else 0
        except ValueError:
            return 0
