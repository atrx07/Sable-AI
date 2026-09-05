"""Unified tool executor and permission-enforced dispatch."""

from __future__ import annotations

from typing import Any

from ..security import PermissionPolicy
from ..tool_schemas import TOOL_SCHEMAS
from .base import ToolCore, ToolResult
from .commands import CommandMixin
from .context import ContextToolMixin
from .files_read import ReadFileMixin
from .files_write import WriteFileMixin
from .git import GitMixin


class ToolExecutor(ContextToolMixin, CommandMixin, ReadFileMixin, WriteFileMixin, GitMixin, ToolCore):
    def dispatch(self, tool_name: str, args: dict[str, Any], mode: str = "build") -> ToolResult:
        exposed_tools = {item["function"]["name"] for item in TOOL_SCHEMAS}
        if tool_name not in exposed_tools:
            result = ToolResult(tool_name, False, error=f"Tool is not exposed to the model: {tool_name}", risk="blocked")
            self.transactions.record_action(tool_name, risk=result.risk, success=False)
            return result
        policy = PermissionPolicy(mode)
        allowed, reason = policy.check(tool_name, args)
        if not allowed:
            result = ToolResult(tool_name, False, error=reason, approval_required=True, risk="high")
            self.transactions.record_action(
                tool_name, risk=result.risk, approval_required=True, success=False,
            )
            return result

        mapping = {
            "read_file": lambda a: self.read_file(a["path"]),
            "read_file_lines": lambda a: self.read_file_lines(a["path"], a.get("start", 1), a.get("end")),
            "list_files": lambda a: self.list_files(a.get("path", ".")),
            "search_files": lambda a: self.search_files(a["pattern"], a.get("path", ".")),
            "grep_files": lambda a: self.grep_files(a["text"], a.get("path", "."), a.get("ext", "")),
            "file_info": lambda a: self.file_info(a["path"]),
            "project_profile": lambda a: self.project_profile(),
            "repo_map": lambda a: self.repo_map(),
            "list_symbols": lambda a: self.list_symbols(a.get("path", "")),
            "find_symbol": lambda a: self.find_symbol(a["name"]),
            "find_references": lambda a: self.find_references(a["name"]),
            "read_symbol": lambda a: self.read_symbol(a["name"], a.get("path", "")),
            "find_tests_for_file": lambda a: self.find_tests_for_file(a["path"]),
            "recent_changes": lambda a: self.recent_changes(),
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
            "git_commit": lambda a: self.git_commit(a["message"]),
            "git_push": lambda a: self.git_push(a.get("branch", "")),
            "git_pull": lambda a: self.git_pull(a.get("branch", "")),
        }
        fn = mapping.get(tool_name)
        if fn is None:
            result = ToolResult(tool_name, False, error=f"Unknown tool: {tool_name}")
            self.transactions.record_action(tool_name, risk="blocked", success=False)
            return result
        try:
            result = fn(args)
        except KeyError as exc:
            result = ToolResult(tool_name, False, error=f"Missing required argument: {exc}")
        except Exception as exc:  # final containment boundary for model-provided input
            result = ToolResult(tool_name, False, error=f"Tool execution error: {exc}")
        self.transactions.record_action(
            tool_name,
            risk=result.risk,
            approval_required=result.approval_required,
            success=result.success,
        )
        return result
