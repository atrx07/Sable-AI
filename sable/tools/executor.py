"""Unified tool executor and permission-enforced dispatch."""

from __future__ import annotations

from typing import Any

from ..capabilities import (
    ActionSource,
    CapabilityPolicy,
    approval_scope_material,
    requirements_for_tool,
)
from ..security import PermissionPolicy
from ..runtime import RuntimeEventType
from ..tool_schemas import TOOL_SCHEMAS
from .base import ToolCore, ToolResult
from .commands import CommandMixin
from .context import ContextToolMixin
from .files_read import ReadFileMixin
from .files_write import WriteFileMixin
from .git import GitMixin


class ToolExecutor(ContextToolMixin, CommandMixin, ReadFileMixin, WriteFileMixin, GitMixin, ToolCore):
    def dispatch(
        self,
        tool_name: str,
        args: dict[str, Any],
        mode: str = "build",
        *,
        source: ActionSource = ActionSource.MODEL,
        task_id: str | None = None,
    ) -> ToolResult:
        exposed_tools = {item["function"]["name"] for item in TOOL_SCHEMAS}
        if tool_name not in exposed_tools:
            result = ToolResult(tool_name, False, error=f"Tool is not exposed to the model: {tool_name}", risk="blocked")
            self.transactions.record_action(tool_name, risk=result.risk, success=False)
            return result
        args = dict(args)
        if tool_name in {"git_push", "git_pull"} and not str(args.get("branch", "")).strip():
            # Bind approval scope to the branch resolved at request time. A blank
            # branch must not become a floating session grant after checkout.
            args["branch"] = self.current_branch()
        try:
            source = source if isinstance(source, ActionSource) else ActionSource(str(source).upper())
        except ValueError:
            source = ActionSource.MODEL
        requirements = requirements_for_tool(tool_name, args)
        required_capabilities = [item.capability.value for item in requirements]
        policy = PermissionPolicy(mode)
        allowed, reason = policy.validate(tool_name, args)
        if not allowed:
            for capability in required_capabilities:
                self._emit_runtime_event(
                    RuntimeEventType.CAPABILITY_DENIED,
                    capability=capability,
                    source=source.value,
                    tool=tool_name,
                    risk="blocked",
                    allowed_by="hard_validation",
                    approval_required=False,
                )
            result = ToolResult(
                tool_name,
                False,
                error=reason,
                approval_required=True,
                risk="blocked",
                security={
                    "source": source.value,
                    "allowed": False,
                    "allowed_by": "hard_validation",
                    "required_capabilities": required_capabilities,
                },
            )
            self.transactions.record_action(
                tool_name,
                risk=result.risk,
                capability=",".join(required_capabilities),
                approval_required=True,
                success=False,
            )
            return result

        security_outcomes: list[dict[str, Any]] = []
        capability_policy = CapabilityPolicy()
        for requirement in requirements:
            outcome = capability_policy.evaluate(requirement.capability, mode=mode, source=source)
            if outcome.approval_required:
                request = self.approvals.create_request(
                    requirement,
                    source=source,
                    tool=tool_name,
                    task_id=task_id or self.runtime_task_id,
                    scope_material=approval_scope_material(tool_name, args, requirement.capability),
                )
                self._emit_runtime_event(
                    RuntimeEventType.CAPABILITY_REQUESTED,
                    capability=requirement.capability.value,
                    source=source.value,
                    tool=tool_name,
                    risk=requirement.risk,
                    approval_scope="exact_action",
                )
                outcome = self.approvals.authorize(request)
            approval_scope = (
                "once" if outcome.allowed_by == "once"
                else "session" if outcome.allowed_by == "session"
                else "not_applicable"
            )
            self._emit_runtime_event(
                RuntimeEventType.CAPABILITY_APPROVED if outcome.allowed else RuntimeEventType.CAPABILITY_DENIED,
                capability=requirement.capability.value,
                source=source.value,
                tool=tool_name,
                risk=requirement.risk,
                allowed_by=outcome.allowed_by,
                approval_required=outcome.approval_required,
                approval_scope=approval_scope,
                decision=(outcome.request.decision.value if outcome.request and outcome.request.decision else None),
            )
            security_outcomes.append(outcome.to_dict())
            if not outcome.allowed:
                result = ToolResult(
                    tool_name,
                    False,
                    error=outcome.reason,
                    approval_required=outcome.approval_required,
                    risk="blocked",
                    security={
                        "source": source.value,
                        "allowed": False,
                        "required_capabilities": required_capabilities,
                        "authorizations": security_outcomes,
                    },
                )
                self.transactions.record_action(
                    tool_name,
                    risk=result.risk,
                    capability=requirement.capability.value,
                    approval_required=result.approval_required,
                    success=False,
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
                sanitize_env=True,
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
        previous_context = dict(self._runtime_action_context)
        self._runtime_action_context = {
            "tool": tool_name,
            "source": source.value,
            "capabilities": required_capabilities,
        }
        try:
            result = fn(args)
        except KeyError as exc:
            result = ToolResult(tool_name, False, error=f"Missing required argument: {exc}")
        except Exception as exc:  # final containment boundary for model-provided input
            result = ToolResult(tool_name, False, error=f"Tool execution error: {exc}")
        finally:
            self._runtime_action_context = previous_context
        tool_boundary_denied = not result.success and result.risk == "blocked"
        result.security = {
            "source": source.value,
            "allowed": not tool_boundary_denied,
            "allowed_by": "tool_boundary" if tool_boundary_denied else "capability_policy",
            "required_capabilities": required_capabilities,
            "authorizations": security_outcomes,
        }
        if tool_boundary_denied:
            for capability in required_capabilities:
                self._emit_runtime_event(
                    RuntimeEventType.CAPABILITY_DENIED,
                    capability=capability,
                    source=source.value,
                    tool=tool_name,
                    risk="blocked",
                    allowed_by="tool_boundary",
                    approval_required=False,
                )
        self.transactions.record_action(
            tool_name,
            risk=result.risk,
            capability=",".join(item["capability"] for item in security_outcomes),
            approval_required=result.approval_required,
            success=result.success,
        )
        return result
