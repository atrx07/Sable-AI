"""Git tools using ambient Git authentication; Sable stores no PATs."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from ..config import contains_secret, redact_secrets
from ..execution import EnvironmentPolicy, ExecutionRequest
from ..security import WorkspaceViolation, git_environment, safe_git_argv
from .base import ToolResult

NO_REMOTE_MESSAGE = (
    "No remote named 'origin' is configured for this repository. "
    "Add one with: git remote add origin <url> — then push again."
)

DETACHED_HEAD_MESSAGE = "Detached HEAD — check out a branch first."


class GitMixin:
    MAX_SECRET_SCAN_BYTES = 10 * 1024 * 1024

    def _git_raw(
        self,
        args: list[str],
        tool: str,
        *,
        cwd: str | Path | None = None,
    ) -> ToolResult:
        # Runtime-driven Git never executes repo hooks (an untrusted repo's
        # pre-commit hook must not run with ambient credentials), never runs
        # a repo-configured filesystem monitor (a monitor script runs on
        # routine index-refreshing reads that hook suppression does not
        # cover), and its environment is the ambient env minus secrets,
        # with Git auth kept. core.hooksPath=/dev/null means "no hooks
        # directory exists"; core.fsmonitor=false disables the monitor.
        return self._run(
            safe_git_argv(args),
            cwd=cwd or self.workspace.cwd,
            tool=tool,
            env=git_environment(),
            environment_policy=EnvironmentPolicy.AMBIENT,
        )

    def _git_repo_root(self, tool: str) -> tuple[Path | None, ToolResult | None]:
        """Resolve the active repository from Sable's current cwd without escaping the workspace."""
        result = self._git_raw(["rev-parse", "--show-toplevel"], tool)
        if not result.success:
            return None, ToolResult(tool, False, error=result.error or "Not a git repository.")
        try:
            root = Path(result.output.strip()).resolve()
            root.relative_to(self.workspace.root)
        except (OSError, ValueError):
            return None, ToolResult(
                tool,
                False,
                error="Refused: active Git repository root is outside the Sable workspace.",
                risk="blocked",
            )
        return root, None

    def _git(self, args: list[str], tool: str) -> ToolResult:
        # These commands are valid before a repository exists.
        if args and args[0] in {"init", "clone", "check-ref-format"}:
            return self._git_raw(args, tool)
        _, denied = self._git_repo_root(tool)
        if denied:
            return denied
        return self._git_raw(args, tool)

    def git_init(self, remote: str | None = None) -> ToolResult:
        git_dir = self.workspace.cwd / ".git"
        if git_dir.exists():
            result = ToolResult(
                "git_init",
                True,
                output="Git repository already initialized in current directory.",
            )
        else:
            result = self._git_raw(["init", "-b", "main"], "git_init")
            if not result.success:
                # Older git versions may not support -b.
                result = self._git_raw(["init"], "git_init")
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
            return ToolResult(
                tool, False, error="Git remote URL cannot start with '-'.", risk="blocked"
            )
        if contains_secret(value):
            return ToolResult(
                tool,
                False,
                error="Refused: remote URL appears to contain embedded credentials.",
                risk="blocked",
            )
        return None

    def _validate_git_branch(self, branch: str, tool: str) -> ToolResult | None:
        branch = str(branch or "").strip()
        if not branch:
            return ToolResult(tool, False, error="Git branch cannot be empty.")
        if branch.startswith("-"):
            return ToolResult(
                tool, False, error="Git branch cannot start with '-'.", risk="blocked"
            )
        check = self._git_raw(["check-ref-format", "--branch", branch], tool)
        if not check.success:
            return ToolResult(
                tool, False, error=f"Invalid Git branch name: {branch}", risk="blocked"
            )
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
        repo_root, denied = self._git_repo_root("git_add")
        if denied:
            return denied
        assert repo_root is not None

        safe: list[str] = []
        for path in paths:
            try:
                # Agent tool results are workspace-root-relative, even after /cd.
                target = self.workspace.resolve(path, base=self.workspace.root)
                rel = target.relative_to(repo_root)
            except WorkspaceViolation as exc:
                return ToolResult("git_add", False, error=str(exc))
            except ValueError:
                return ToolResult(
                    "git_add",
                    False,
                    error=f"Changed path '{path}' is outside the active Git repository.",
                    risk="blocked",
                )
            safe.append(rel.as_posix() or ".")

        if not safe:
            return ToolResult("git_add", True, output="Nothing to stage.")
        return self._git_raw(
            ["add", "-A", "--", *sorted(set(safe))],
            "git_add",
            cwd=repo_root,
        )

    def git_staged_paths(self) -> list[str]:
        """Return staged paths for the active repo, or an empty list outside Git."""
        result = self._git(["diff", "--cached", "--name-only", "--no-ext-diff"], "git_status")
        if not result.success:
            return []
        return [line.strip() for line in result.output.splitlines() if line.strip()]

    def git_commit(self, message: str) -> ToolResult:
        if contains_secret(message):
            return ToolResult(
                "git_commit", False, error="Commit aborted: message appears to contain a secret."
            )
        repo_root, denied = self._git_repo_root("git_commit")
        if denied:
            return denied
        assert repo_root is not None
        try:
            scan = self.execution_backend.execute(
                ExecutionRequest(
                    # Same safety overrides as _git_raw: this diff refreshes
                    # the index, so a repo fsmonitor would run without them.
                    argv=safe_git_argv(["diff", "--cached", "--no-ext-diff", "--no-textconv"]),
                    cwd=repo_root,
                    timeout_seconds=self.command_timeout,
                    env=git_environment(),
                    environment_policy=EnvironmentPolicy.AMBIENT,
                    max_output_chars=self.MAX_SECRET_SCAN_BYTES + 1,
                )
            )
        except (OSError, RuntimeError, ValueError) as exc:
            return ToolResult(
                "git_commit", False, error=f"Commit aborted: staged secret scan failed: {exc}"
            )
        if not scan.success:
            return ToolResult(
                "git_commit",
                False,
                error="Commit aborted: staged diff could not be inspected safely.",
            )
        raw_diff = scan.output
        if scan.truncated or len(raw_diff.encode("utf-8")) > self.MAX_SECRET_SCAN_BYTES:
            return ToolResult(
                "git_commit",
                False,
                error="Commit aborted: staged diff exceeds the bounded secret-scan limit.",
            )
        if contains_secret(raw_diff):
            return ToolResult(
                "git_commit", False, error="Commit aborted: staged diff contains a likely secret."
            )
        result = self._git(["commit", "-m", message], "git_commit")
        if not result.success and (
            "nothing to commit" in result.error.lower() or "nothing added" in result.error.lower()
        ):
            # Success with an explicit outcome: no commit was created. Callers
            # must not read this as a commit by parsing the output string.
            return ToolResult(
                "git_commit",
                True,
                output="Nothing new to commit.",
                data={"outcome": "nothing_to_commit"},
            )
        if result.success:
            latest = self._git(["log", "--oneline", "-1"], "git_commit")
            if latest.success and latest.output.strip():
                result.output = f"Committed: {latest.output.strip()}"
                result.data = {
                    "outcome": "committed",
                    "commit_sha": latest.output.strip().split()[0],
                }
            else:
                result.data = {"outcome": "committed"}
        return result

    def git_head_sha(self) -> str | None:
        result = self._git(["rev-parse", "HEAD"], "git_status")
        return result.output.strip() if result.success and result.output.strip() else None

    def current_branch(self) -> str:
        # No silent fallback: a detached HEAD resolves to "" so branch-dependent
        # operations fail closed instead of silently acting on "main".
        result = self._git(["branch", "--show-current"], "git_branch")
        return result.output.strip() if result.success and result.output.strip() else ""

    def _exec_config_risk(self, key: str, value: str) -> str | None:
        """Return why a repo config key can execute code, or None if safe.

        F R2-F1: a crafted repo config runs arbitrary code on remote ops
        via credential helpers, ssh commands, proxies, fsmonitor, include
        indirection, or diff/textconv drivers. Only the exec-capable form
        of each key is flagged (a boolean fsmonitor, a user-scoped
        protocol allow, a plain credential helper are all fine).
        """
        if key == "credential.helper" and value.startswith("!"):
            return "shell credential helper"
        if key in {"core.sshcommand", "core.gitproxy"} and value:
            return "external command"
        if key == "core.fsmonitor" and value and value.lower() not in {"true", "false"}:
            return "external fsmonitor hook"
        if key == "include.path" or (key.startswith("includeif.") and key.endswith(".path")):
            return "config include indirection"
        if key.startswith("protocol.") and key.endswith(".allow"):
            if value.lower() == "always":
                return "protocol transport allowed unconditionally"
            return None
        if key == "core.attributesfile" and value:
            return "attributes file indirection"
        if key.startswith("diff.") and (key.endswith(".textconv") or key.endswith(".command")):
            if value:
                return "external diff/textconv driver"
            return None
        return None

    def _repo_config_exec_check(self, tool: str) -> ToolResult | None:
        """Refuse remote ops when repo config can execute code (F R2-F1).

        Returns a refusal naming the offending key, or None when the
        config is clean. Bounded: reads ``git config --list -z`` only
        (listing never executes helpers); a config that cannot be listed
        fails closed. The scan covers the workspace repo's config,
        including keys pulled in through include.path indirection.
        """
        repo_root, denied = self._git_repo_root(tool)
        if denied or repo_root is None:
            return denied
        result = self._git_raw(["config", "--list", "-z"], tool, cwd=repo_root)
        if not result.success:
            return ToolResult(
                tool,
                False,
                error=(
                    "Refused: repository config could not be inspected before a remote operation."
                ),
                risk="blocked",
            )
        for entry in result.output.split("\0"):
            if not entry:
                continue
            key, sep, value = entry.partition("\n")
            risk = self._exec_config_risk(key.strip().lower(), value.strip() if sep else "")
            if risk:
                return ToolResult(
                    tool,
                    False,
                    error=(
                        f"Refused: repository config key '{key.strip()}' can execute "
                        f"code ({risk}). Remove it before pushing, pulling, or "
                        "fetching."
                    ),
                    risk="blocked",
                )
        return None

    def git_push(self, branch: str = "") -> ToolResult:
        branch = branch or self.current_branch()
        if not branch:
            return ToolResult("git_push", False, error=DETACHED_HEAD_MESSAGE, risk="high")
        denied = self._validate_git_branch(branch, "git_push")
        if denied:
            denied.risk = "high"
            return denied
        config_risk = self._repo_config_exec_check("git_push")
        if config_risk is not None:
            return config_risk
        remote = self._git(["remote", "get-url", "origin"], "git_push")
        if not remote.success:
            return ToolResult("git_push", False, error=NO_REMOTE_MESSAGE, risk="high")
        result = self._git(["push", "-u", "origin", branch], "git_push")
        result.risk = "high"
        return result

    def git_pull(self, branch: str = "") -> ToolResult:
        branch = branch or self.current_branch()
        if not branch:
            return ToolResult("git_pull", False, error=DETACHED_HEAD_MESSAGE, risk="high")
        denied = self._validate_git_branch(branch, "git_pull")
        if denied:
            denied.risk = "high"
            return denied
        config_risk = self._repo_config_exec_check("git_pull")
        if config_risk is not None:
            return config_risk
        result = self._git(["pull", "--rebase", "origin", branch], "git_pull")
        result.risk = "high"
        if not result.success:
            # F R2-F4: a conflicting pull leaves the repo mid-rebase. Say
            # so explicitly with the recovery command; never auto-abort.
            repo_root, denied = self._git_repo_root("git_pull")
            if denied is None and repo_root is not None:
                rebase_merge = repo_root / ".git" / "rebase-merge"
                rebase_apply = repo_root / ".git" / "rebase-apply"
                if rebase_merge.exists() or rebase_apply.exists():
                    result.error = (
                        (result.error or "git pull failed")
                        + "\nRepository is mid-rebase after the failed pull "
                        "(rebase state present). To undo the rebase: "
                        "git rebase --abort. To continue after resolving "
                        "conflicts: git rebase --continue."
                    )
        return result

    def git_status(self) -> ToolResult:
        return self._git(["status", "--short"], "git_status")

    def git_log(self, n: int = 10) -> ToolResult:
        return self._git(
            ["log", "--oneline", "--decorate", "--graph", "-n", str(max(1, min(50, int(n))))],
            "git_log",
        )

    def git_diff(self, file: str = "") -> ToolResult:
        # --no-ext-diff keeps external diff drivers out; --no-textconv keeps
        # textconv drivers out (a repo can map paths to a driver through
        # .gitattributes, and a content diff would otherwise run it).
        args = ["diff", "--no-ext-diff", "--no-textconv"]
        if file:
            repo_root, denied = self._git_repo_root("git_diff")
            if denied:
                return denied
            assert repo_root is not None
            try:
                target = self._resolve(file)
                rel = target.relative_to(repo_root)
            except WorkspaceViolation as exc:
                return ToolResult("git_diff", False, error=str(exc))
            except ValueError:
                return ToolResult(
                    "git_diff",
                    False,
                    error=f"Path '{file}' is outside the active Git repository.",
                    risk="blocked",
                )
            result = self._git_raw(
                [*args, "--", rel.as_posix()],
                "git_diff",
                cwd=repo_root,
            )
        else:
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
        result = self._git_raw(args, "git_clone")
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

    def git_remote_url(self, name: str = "origin") -> str | None:
        """Resolved remote URL, runtime-owned (never model-supplied).

        Used for approval scoping so a human approving a push sees WHERE
        it goes. Returns None when no such remote is configured. Never
        raises; callers redact the URL before display.
        """
        result = self._git(["remote", "get-url", name], "git_status")
        url = result.output.strip() if result.success else ""
        return url or None

    def git_state_summary(self) -> dict[str, Any]:
        """Deterministic working-tree state for runtime evidence.

        Read-only and never exposed as a model tool. Remote URLs are never
        returned — only whether a remote is configured. Never raises: any
        failure yields ``{"present": False}``.
        """
        try:
            repo_root, denied = self._git_repo_root("git_status")
            if denied or repo_root is None:
                return {"present": False}
            branch = self.current_branch()
            head = self.git_head_sha()
            staged = self.git_staged_paths()
            unstaged_result = self._git(["diff", "--name-only", "--no-ext-diff"], "git_status")
            unstaged = (
                [line.strip() for line in unstaged_result.output.splitlines() if line.strip()]
                if unstaged_result.success
                else []
            )
            untracked_result = self._git(
                ["ls-files", "--others", "--exclude-standard"], "git_status"
            )
            untracked = (
                [line.strip() for line in untracked_result.output.splitlines() if line.strip()]
                if untracked_result.success
                else []
            )
            remote = self._git(["remote", "get-url", "origin"], "git_status")
            upstream_result = self._git(
                ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
                "git_status",
            )
            upstream = (upstream_result.output.strip() if upstream_result.success else "") or None
            # Ahead is only meaningful against an upstream branch; without
            # one it is None (matching the context engine), so the evidence
            # block omits it instead of reporting a bogus "ahead 0".
            ahead = self.git_ahead_count(branch) if branch and upstream else None
            return {
                "present": True,
                "branch": branch or None,
                "head": head,
                "staged": staged,
                "unstaged": unstaged,
                "untracked": untracked,
                "remote": bool(remote.success),
                "upstream": upstream,
                "ahead": ahead,
            }
        except Exception:
            return {"present": False}
