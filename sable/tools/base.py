"""Shared tool result and executor primitives."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..capabilities import ApprovalEngine, ApprovalHandler
from ..config import is_blocked_path, redact_secrets
from ..context import ContextEngine
from ..execution import EnvironmentPolicy, ExecutionBackend, ExecutionRequest, select_execution_backend
from ..project import ProjectInspector
from ..runtime import RuntimeEventType
from ..security import Workspace, WorkspaceViolation
from ..transactions import TransactionError, TransactionStatus, WorkspaceTransactionManager

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
    execution: dict[str, Any] = field(default_factory=dict)
    security: dict[str, Any] = field(default_factory=dict)

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
            "execution": dict(self.execution),
            "security": dict(self.security),
        }


class ToolCore:
    def __init__(
        self,
        project_dir: str,
        command_timeout: int = 120,
        transaction_storage_dir: str | Path | None = None,
        execution_backend: str | ExecutionBackend = "auto",
        proot_rootfs: str | Path | None = None,
        approval_engine: ApprovalEngine | None = None,
        approval_handler: ApprovalHandler | None = None,
    ):
        self.workspace = Workspace(project_dir)
        self.project_dir = str(self.workspace.root)
        self.command_timeout = int(command_timeout)
        self.execution_backend = (
            select_execution_backend(
                execution_backend,
                workspace_root=self.workspace.root,
                proot_rootfs=proot_rootfs,
            )
            if isinstance(execution_backend, str)
            else execution_backend
        )
        self.approvals = approval_engine or ApprovalEngine(handler=approval_handler)
        self.runtime_task_id: str | None = None
        self.runtime_session_id: str | None = None
        self.runtime_event_handler: Callable[[RuntimeEventType, dict[str, Any]], None] | None = None
        self.runtime_event_errors: list[str] = []
        self._runtime_action_context: dict[str, Any] = {}
        self.context_engine = ContextEngine(self.workspace.root)
        self.transactions = WorkspaceTransactionManager(
            self.workspace.root,
            storage_dir=transaction_storage_dir,
        )

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
        omitted = len(text) - MAX_OUTPUT_CHARS
        marker = f"\n... [{omitted} chars omitted] ...\n"
        remaining = max(0, MAX_OUTPUT_CHARS - len(marker))
        left = remaining // 2
        right = remaining - left
        return text[:left] + marker + (text[-right:] if right else ""), True

    def _run(
        self,
        argv: list[str] | str,
        *,
        cwd: str | Path | None = None,
        timeout: int | None = None,
        tool: str = "run_command",
        env: dict[str, str] | None = None,
        shell: bool = False,
        environment_policy: EnvironmentPolicy = EnvironmentPolicy.PROJECT,
        max_output_chars: int = MAX_OUTPUT_CHARS,
    ) -> ToolResult:
        try:
            target_cwd = self._resolve(str(cwd)) if cwd else self.workspace.cwd
        except (WorkspaceViolation, OSError) as exc:
            return ToolResult(tool, False, error=str(exc))

        event_context = dict(self._runtime_action_context)
        event_context.setdefault("tool", tool)
        event_context.setdefault("source", "RUNTIME")
        self._emit_runtime_event(
            RuntimeEventType.BACKEND_SELECTED,
            backend=self.execution_backend.name,
            available=self.execution_backend.availability().available,
            guarantees=self.execution_backend.guarantees.to_dict(),
            **event_context,
        )
        self._emit_runtime_event(
            RuntimeEventType.PROCESS_STARTED,
            backend=self.execution_backend.name,
            shell=bool(shell),
            environment_policy=environment_policy.value,
            cwd=self._rel(target_cwd),
            **event_context,
        )
        try:
            execution = self.execution_backend.execute(
                ExecutionRequest(
                    argv=argv,
                    cwd=target_cwd,
                    timeout_seconds=int(timeout or self.command_timeout),
                    shell=shell,
                    env=env,
                    environment_policy=environment_policy,
                    max_output_chars=max_output_chars,
                )
            )
        except KeyboardInterrupt:
            self._emit_runtime_event(
                RuntimeEventType.PROCESS_TERMINATED,
                backend=self.execution_backend.name,
                success=False,
                interrupted=True,
                **event_context,
            )
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            self._emit_runtime_event(
                RuntimeEventType.PROCESS_COMPLETED,
                backend=self.execution_backend.name,
                success=False,
                failure_type=type(exc).__name__,
                **event_context,
            )
            return ToolResult(tool, False, error=redact_secrets(str(exc)))

        completion = {
            "backend": execution.backend,
            "success": execution.success,
            "exit_code": execution.exit_code,
            "duration_ms": execution.duration_ms,
            "truncated": execution.truncated,
            **event_context,
        }
        if execution.timed_out:
            self._emit_runtime_event(RuntimeEventType.PROCESS_TIMEOUT, **completion)
        else:
            self._emit_runtime_event(RuntimeEventType.PROCESS_COMPLETED, **completion)
        if execution.terminated:
            self._emit_runtime_event(
                RuntimeEventType.PROCESS_TERMINATED,
                cleanup_method=execution.metadata.get("cleanup_method", "unknown"),
                descendant_cleanup_confirmed=execution.metadata.get("descendant_cleanup_confirmed", False),
                **completion,
            )

        output, output_truncated = self._trim(execution.output)
        error, error_truncated = self._trim(execution.error)
        return ToolResult(
            tool,
            execution.success,
            output=output,
            error=error,
            exit_code=execution.exit_code,
            duration_ms=execution.duration_ms,
            truncated=execution.truncated or output_truncated or error_truncated,
            execution=execution.execution_metadata(),
        )

    def execution_backend_status(self) -> dict[str, object]:
        return self.execution_backend.status()

    def configure_approvals(self, *, session_id: str | None, handler: ApprovalHandler | None) -> None:
        self.approvals.bind_session(session_id)
        self.approvals.set_handler(handler)
        self.runtime_session_id = session_id

    def set_runtime_identity(self, *, task_id: str | None, session_id: str | None) -> None:
        self.runtime_task_id = task_id
        self.runtime_session_id = session_id

    def set_runtime_event_handler(
        self,
        handler: Callable[[RuntimeEventType, dict[str, Any]], None] | None,
    ) -> None:
        self.runtime_event_handler = handler

    def _emit_runtime_event(self, event_type: RuntimeEventType, **metadata: Any) -> None:
        if self.runtime_event_handler is None:
            return
        try:
            self.runtime_event_handler(event_type, metadata)
        except Exception as exc:
            self.runtime_event_errors.append(redact_secrets(str(exc))[:300])
            self.runtime_event_errors = self.runtime_event_errors[-20:]

    def consume_runtime_event_errors(self) -> list[str]:
        errors = list(self.runtime_event_errors)
        self.runtime_event_errors.clear()
        return errors

    def _safe_path(self, path: str, tool: str) -> tuple[Path | None, ToolResult | None]:
        if is_blocked_path(path):
            return None, ToolResult(tool, False, error=f"Access denied: '{path}' is a protected path.", risk="blocked")
        try:
            target = self._resolve(path)
            resolved_rel = self._rel(target)
            if is_blocked_path(resolved_rel):
                return None, ToolResult(tool, False, error=f"Access denied: '{resolved_rel}' is a protected path.", risk="blocked")
            return target, None
        except WorkspaceViolation as exc:
            return None, ToolResult(tool, False, error=str(exc), risk="blocked")

    def _capture_before_mutation(self, target: Path, tool: str) -> ToolResult | None:
        """Capture local pre-task state before a file tool mutates a path."""
        try:
            self.transactions.capture(target)
            return None
        except (TransactionError, OSError) as exc:
            return ToolResult(
                tool,
                False,
                error=f"Transactional safety check failed: {exc}",
                risk="blocked",
            )

    def _record_mutation(self, changed_files: list[str]) -> None:
        """Record the exact state produced by a successful file mutation."""
        self.transactions.record_mutation(changed_files)

    def begin_transaction(self, label: str = "task") -> str:
        return self.transactions.begin(label)

    def finish_transaction(
        self,
        changed_files: list[str] | None = None,
        *,
        status: str = TransactionStatus.COMPLETED.value,
        verification: dict[str, Any] | None = None,
        commit_sha: str | None = None,
    ) -> dict[str, object]:
        return self.transactions.finish(
            changed_files,
            status=status,
            verification=verification,
            commit_sha=commit_sha,
        )

    def create_transaction_checkpoint(self, label: str = "checkpoint") -> str | None:
        return self.transactions.checkpoint(label)

    def restore_transaction_checkpoint(self, checkpoint_id: str | None = None) -> ToolResult:
        try:
            outcome = self.transactions.restore_checkpoint(checkpoint_id)
        except TransactionError as exc:
            return ToolResult("transaction_checkpoint", False, error=str(exc), risk="high")
        return ToolResult(
            "transaction_checkpoint",
            True,
            output=f"Restored checkpoint {outcome['checkpoint_id']}.",
            changed_files=list(outcome["restored"]),
            risk="high",
        )

    def rollback_active_transaction(self) -> ToolResult:
        try:
            outcome = self.transactions.rollback_current()
        except TransactionError as exc:
            return ToolResult("transaction_rollback", False, error=str(exc), risk="high")
        restored = list(outcome.get("restored", []))
        conflicts = list(outcome.get("conflicts", []))
        errors = list(outcome.get("errors", []))
        success = not conflicts and not errors
        return ToolResult(
            "transaction_rollback",
            success,
            output=("Rolled back active transaction: " + ", ".join(restored)) if success and restored else ("No active transaction changes to roll back." if success else ""),
            error=("Rollback was partial. Conflicts: " + ", ".join(conflicts) + ("; errors: " + "; ".join(errors) if errors else "")) if not success else "",
            changed_files=restored,
            risk="high",
        )

    def undo_transaction(self, transaction_id: str | None = None, *, dry_run: bool = False) -> ToolResult:
        try:
            outcome = self.transactions.undo(transaction_id, dry_run=dry_run)
        except TransactionError as exc:
            return ToolResult("undo", False, error=str(exc), risk="high")
        txid = str(outcome["transaction_id"])
        conflicts = list(outcome.get("conflicts", []))
        errors = list(outcome.get("errors", []))
        if dry_run:
            would = list(outcome.get("would_restore", []))
            detail = f"Undo dry-run for {txid}: would restore {len(would)} path(s)"
            if conflicts:
                detail += f"; {len(conflicts)} conflict(s) would be skipped"
            return ToolResult("undo", True, output=detail + ".", changed_files=would, risk="high")
        restored = list(outcome.get("restored", []))
        success = not errors
        detail = f"Undid Sable transaction {txid}. Restored {len(restored)} path(s)."
        if conflicts:
            detail += f" Preserved {len(conflicts)} conflicting path(s): {', '.join(conflicts[:8])}."
        detail += " Git history was not rewritten."
        return ToolResult(
            "undo",
            success,
            output=detail if success else "",
            error=("Rollback errors: " + "; ".join(errors)) if errors else "",
            changed_files=restored,
            risk="high",
        )

    def undo_last_transaction(self) -> ToolResult:
        return self.undo_transaction()

    def transaction_status(self, transaction_id: str | None = None) -> ToolResult:
        try:
            status = self.transactions.status(transaction_id)
        except TransactionError as exc:
            return ToolResult("transaction_status", False, error=str(exc))
        return ToolResult("transaction_status", True, output=json.dumps(status, indent=2))

    def transaction_list(self, limit: int = 10) -> ToolResult:
        return ToolResult(
            "transaction_status",
            True,
            output=json.dumps(self.transactions.list_transactions(limit), indent=2),
        )

    def project_profile(self) -> ToolResult:
        inspector = ProjectInspector(self.project_dir)
        return ToolResult("project_profile", True, output=json.dumps(inspector.profile(), indent=2))
