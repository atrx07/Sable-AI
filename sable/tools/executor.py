"""Unified tool executor and permission-enforced dispatch."""

from __future__ import annotations

from typing import Any

from ..security import PermissionPolicy
from .base import ToolCore, ToolResult
from .commands import CommandMixin
from .files_read import ReadFileMixin
from .files_write import WriteFileMixin
from .git import GitMixin


class ToolExecutor(CommandMixin, ReadFileMixin, WriteFileMixin, GitMixin, ToolCore):
    def dispatch(self, tool_name: str, args: dict[str, Any], mode: str = "build") -> ToolResult:
        policy = PermissionPolicy(mode)
        allowed, reason = policy.check(tool_name, args)
        if not allowed:
            return ToolResult(tool_name, False, error=reason, approval_required=True, risk="high")

        mapping = {
            "read_file": lambda a: self.read_file(a["path"]),
            "read_file_lines": lambda a: self.read_file_lines(a["path"], a.get("start", 1), a.get("end")),
            "list_files": lambda a: self.list_files(a.get("path", ".")),
            "search_files": lambda a: self.search_files(a["pattern"], a.get("path", ".")),
            "grep_files": lambda a: self.grep_files(a["text"], a.get("path", "."), a.get("ext", "")),
            "file_info": lambda a: self.file_info(a["path"]),
            "project_profile": lambda a: self.project_profile(),
            "write_file": lambda a: self.write_file(a["path"], a["content"]),
            "append_file": lambda a: self.append_file(a["path"], a["content"]),
            "patch_file": lambda a: self.patch_file(a["path"], a["old"], a["new"]),
            "apply_patch": lambda a: self.apply_patch(a["patch"]),
            "make_dir": lambda a: self.make_dir(a["path"]),
            "copy_file": lambda a: self.copy_file(a["src"], a["dst"]),
            "move_file": lambda a: self.move_file(a["src"], a["dst"]),
            "delete_file": lambda a: self.delete_file(a["path"]),
            "run_command": lambda a: self.run_command(
                a["argv"],
                a.get("cwd", "."),
                a.get("timeout"),
                sanitize_env=(mode != "yolo"),
            ),
            "run_shell": lambda a: self.run_shell(a["command"], a.get("cwd", "."), a.get("timeout")),
            "git_status": lambda a: self.git_status(),
            "git_diff": lambda a: self.git_diff(a.get("file", "")),
            "git_log": lambda a: self.git_log(a.get("n", 10)),
            "git_branch": lambda a: self.git_branch(a.get("name", "")),
            "git_add": lambda a: self.git_add(a.get("files", ".")),
            "git_commit": lambda a: self.git_commit(a["message"]),
            "git_push": lambda a: self.git_push(a.get("branch", "")),
            "git_pull": lambda a: self.git_pull(a.get("branch", "")),
        }
        fn = mapping.get(tool_name)
        if fn is None:
            return ToolResult(tool_name, False, error=f"Unknown tool: {tool_name}")
        try:
            return fn(args)
        except KeyError as exc:
            return ToolResult(tool_name, False, error=f"Missing required argument: {exc}")
        except Exception as exc:  # final containment boundary for model-provided input
            return ToolResult(tool_name, False, error=f"Tool execution error: {exc}")
