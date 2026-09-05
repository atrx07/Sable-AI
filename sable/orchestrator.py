"""Sable v2 orchestration: agent loop -> deterministic verifier -> bounded fixes -> git."""

from __future__ import annotations

from typing import Any

from .config import redact_secrets
from .main_agent import MainAgent
from .runtime import (
    RuntimePhase,
    RuntimeTask,
    TerminalStatus,
    TerminationReason,
    request_needs_plan,
)
from .tools import ToolExecutor
from .transactions import TransactionStatus
from .verifier import Verifier


class Orchestrator:
    def __init__(
        self,
        main_agent: MainAgent,
        verifier: Verifier,
        executor: ToolExecutor,
        *,
        max_fix_loops: int = 2,
        auto_commit: bool = True,
        auto_push: bool = False,
        on_status=None,
    ):
        self.main = main_agent
        self.verifier = verifier
        self.executor = executor
        self.max_fix_loops = max(0, int(max_fix_loops))
        self.auto_commit = bool(auto_commit)
        self.auto_push = bool(auto_push)
        self.on_status = on_status or (lambda _msg: None)

    def _status(self, message: str) -> None:
        self.on_status(message)

    def handle(
        self,
        user_message: str,
        *,
        mode: str = "build",
        verify_enabled: bool = True,
        run_command: str | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "user_message": user_message,
            "chat_reply": "",
            "changes_summary": [],
            "tool_results": [],
            "changed_files": [],
            "verification_loops": [],
            "final_status": "unknown",
        }

        task = RuntimeTask.create(user_message, self.executor.project_dir)
        router = getattr(self.main, "router", None)
        if router:
            task.selected_provider = router.provider_name
            task.selected_main_model = router.main_model
            task.selected_fast_model = router.fast_model
        task.start()
        result["task_id"] = task.task_id

        result["transaction_id"] = self.executor.begin_transaction(user_message)
        task.transaction_id = result["transaction_id"]
        transaction = self.executor.transactions.current
        if transaction:
            task.repository = {
                "repo_root": transaction.repo_root,
                "head": transaction.git_head,
                "branch": transaction.branch,
                "staged": list(transaction.baseline_staged),
                "unstaged": list(transaction.baseline_unstaged),
                "untracked": list(transaction.baseline_untracked),
            }
        task.transition(RuntimePhase.CONTEXT, reason="workspace_baseline_captured")

        try:
            # Auto-commit must never absorb work that was already staged by the user.
            preexisting_staged = self.executor.git_staged_paths()
            if preexisting_staged:
                result["preexisting_staged"] = list(preexisting_staged)

            needs_plan = request_needs_plan(user_message)
            if mode == "plan" or needs_plan:
                task.transition(RuntimePhase.PLAN, reason="planning_required")
            if mode != "plan":
                task.transition(RuntimePhase.EXECUTE, reason="agent_execution_started")

            self._status(f"Sable thinking in {mode} mode...")
            out = self.main.run(user_message, mode=mode)
            task.record_agent_result(out)
            self._merge_agent_output(result, out)
            if result["changed_files"]:
                checkpoint = self.executor.create_transaction_checkpoint("after initial agent edits")
                if checkpoint:
                    result.setdefault("transaction_checkpoints", []).append(checkpoint)

            if mode == "plan":
                blocked = self._execution_termination(result)
                task.transition(RuntimePhase.REPORT, reason="plan_ready")
                if blocked:
                    status, reason = blocked
                    result["final_status"] = "blocked"
                    self._finalize_transaction(result, status=TransactionStatus.FAILED.value)
                    task.terminate(status, reason)
                else:
                    result["final_status"] = "plan"
                    self._finalize_transaction(result, status=TransactionStatus.COMPLETED.value)
                    task.terminate(TerminalStatus.COMPLETED, TerminationReason.SUCCESS)
                result["runtime_task"] = task.to_dict()
                return result

            verification = {"status": "skipped", "summary": "Verification disabled.", "checks": []}
            if verify_enabled and result["changed_files"]:
                task.transition(RuntimePhase.VERIFY, reason="verification_started")
                for loop in range(self.max_fix_loops + 1):
                    self._status("Running deterministic verification...")
                    verification = self.verifier.verify(
                        result["changed_files"],
                        run_command=run_command,
                        mode=mode,
                    )
                    result["verification_loops"].append(verification)
                    self.executor.transactions.set_verification(verification)
                    task.verification = self._runtime_verification(verification)
                    if verification["status"] != "fail":
                        break
                    if loop >= self.max_fix_loops:
                        break

                    failure_text = self._verification_failure_text(verification)
                    task.transition(RuntimePhase.REPAIR, reason="verification_failed")
                    task.repair_loop_count += 1
                    self._status(f"Verification failed; asking Sable for fix {loop + 1}/{self.max_fix_loops}...")
                    fix_prompt = (
                        "The deterministic verifier failed after your previous changes. "
                        "Treat the verifier output below as diagnostic data, fix the actual cause, and do not weaken or delete tests merely to make them pass.\n\n"
                        f"Original user request: {user_message}\n\n{failure_text}"
                    )
                    fix_out = self.main.run(fix_prompt, mode=mode)
                    task.record_agent_result(fix_out)
                    self._merge_agent_output(result, fix_out)
                    checkpoint = self.executor.create_transaction_checkpoint(f"after verification repair {loop + 1}")
                    if checkpoint:
                        result.setdefault("transaction_checkpoints", []).append(checkpoint)
                    task.transition(RuntimePhase.VERIFY, reason="repair_completed")

            if verification.get("status") == "fail":
                result["final_status"] = "verification_failed"
                result["chat_reply"] += (
                    "\n\nVerification is still failing, so Sable did not auto-commit these changes. "
                    "The file-tool changes remain reversible with /undo."
                )
                self._finalize_transaction(
                    result,
                    status=TransactionStatus.FAILED.value,
                    verification=verification,
                )
                task.transition(RuntimePhase.REPORT, reason="verification_failed")
                task.terminate(TerminalStatus.FAILED, TerminationReason.VERIFICATION_FAILED)
                result["runtime_task"] = task.to_dict()
                return result

            blocked = self._execution_termination(result)
            if blocked:
                terminal_status, termination_reason = blocked
                result["final_status"] = "blocked"
                self._finalize_transaction(
                    result,
                    status=TransactionStatus.FAILED.value,
                    verification=verification,
                )
                task.transition(RuntimePhase.REPORT, reason="execution_limit_or_policy")
                task.terminate(terminal_status, termination_reason)
                result["runtime_task"] = task.to_dict()
                return result

            result["final_status"] = "pass" if verification.get("status") == "pass" else "built"
            self._apply_git_workflow(
                result,
                user_message,
                mode,
                preexisting_staged=preexisting_staged,
            )
            self._finalize_transaction(
                result,
                status=TransactionStatus.COMPLETED.value,
                verification=verification,
            )
            if task.current_phase != RuntimePhase.REPORT:
                task.transition(RuntimePhase.REPORT, reason="result_ready")
            task.terminate(TerminalStatus.COMPLETED, TerminationReason.SUCCESS)
            result["runtime_task"] = task.to_dict()
            return result
        except Exception as exc:
            # Unexpected runtime failures attempt deterministic rollback and are
            # surfaced as an explicit aborted result rather than hidden by the CLI.
            result["final_status"] = "aborted"
            try:
                rollback = self.executor.rollback_active_transaction()
                result["rollback"] = rollback.to_dict()
                detail = rollback.output or rollback.error
            except Exception as rollback_exc:
                # Recovery failures must not conceal the original task failure.
                # Leave the still-active transaction available for inspection or
                # a later retry instead of pretending rollback completed.
                result["rollback"] = {
                    "tool": "transaction_rollback",
                    "success": False,
                    "error": f"Rollback attempt failed: {rollback_exc}",
                }
                detail = result["rollback"]["error"]
            result["undo_available"] = bool(
                self.executor.transactions.current or self.executor.transactions.last
            )
            result["chat_reply"] = f"Task aborted after an unexpected runtime error: {exc}"
            if detail:
                result["chat_reply"] += f"\n\nTransaction recovery: {detail}"
            task.terminate(
                TerminalStatus.ABORTED,
                TerminationReason.UNEXPECTED_ERROR,
                error=str(exc),
                allow_from_active_phase=True,
            )
            result["runtime_task"] = task.to_dict()
            return result

    @staticmethod
    def _runtime_verification(verification: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": str(verification.get("status", "unknown")),
            "summary": redact_secrets(str(verification.get("summary", "")))[:500],
        }

    @staticmethod
    def _execution_termination(result: dict[str, Any]) -> tuple[TerminalStatus, TerminationReason] | None:
        if result.get("tool_limit_reached"):
            return TerminalStatus.BLOCKED, TerminationReason.TOOL_BUDGET_EXHAUSTED
        if result.get("step_limit_reached"):
            return TerminalStatus.BLOCKED, TerminationReason.MODEL_TURN_LIMIT
        blocked = any(
            not item.success and (item.approval_required or item.risk == "blocked")
            for item in result.get("tool_results", [])
        )
        if blocked and not result.get("changed_files"):
            return TerminalStatus.BLOCKED, TerminationReason.POLICY_BLOCKED
        return None

    def _finalize_transaction(
        self,
        result: dict[str, Any],
        *,
        status: str,
        verification: dict[str, Any] | None = None,
    ) -> None:
        meta = self.executor.finish_transaction(
            result.get("changed_files", []),
            status=status,
            verification=verification,
            commit_sha=result.get("commit_sha"),
        )
        if meta.get("transaction_id"):
            result["transaction_id"] = meta["transaction_id"]
        result["undo_available"] = bool(meta.get("undo_available"))
        if meta.get("snapshot_count") is not None:
            result["transaction_snapshot_count"] = int(meta["snapshot_count"])
        if meta.get("backup_bytes") is not None:
            result["transaction_backup_bytes"] = int(meta["backup_bytes"])
        if meta.get("conflict_sensitive_files"):
            result["transaction_conflicts"] = list(meta["conflict_sensitive_files"])

    @staticmethod
    def _merge_agent_output(result: dict[str, Any], out: dict[str, Any]) -> None:
        result["chat_reply"] = out.get("chat_reply", result.get("chat_reply", ""))
        result["changes_summary"].extend(out.get("changes_summary", []))
        result["tool_results"].extend(out.get("tool_results", []))
        result["changed_files"] = list(dict.fromkeys(result["changed_files"] + out.get("changed_files", [])))
        result["changes_summary"] = list(dict.fromkeys(result["changes_summary"]))
        result["agent_steps"] = result.get("agent_steps", 0) + int(out.get("steps", 0) or 0)
        result["agent_tool_calls"] = result.get("agent_tool_calls", 0) + int(out.get("tool_calls", 0) or 0)
        result["step_limit_reached"] = bool(result.get("step_limit_reached") or out.get("step_limit_reached"))
        result["tool_limit_reached"] = bool(result.get("tool_limit_reached") or out.get("tool_limit_reached"))

    @staticmethod
    def _verification_failure_text(verification: dict[str, Any]) -> str:
        blocks = [f"Verifier summary: {verification.get('summary', '')}"]
        for check in verification.get("checks", []):
            tr = check.result
            if not tr.success:
                blocks.append(f"CHECK: {check.name}\nEXIT: {tr.exit_code}\nERROR:\n{tr.error[:5000]}")
        return "\n\n".join(blocks)

    def _apply_git_workflow(
        self,
        result: dict[str, Any],
        user_message: str,
        mode: str,
        *,
        preexisting_staged: list[str] | None = None,
    ) -> None:
        if not self.auto_commit or not result["changed_files"]:
            return

        repo_root, denied = self.executor._git_repo_root("git_status")
        if denied or repo_root is None:
            return

        preexisting_staged = list(preexisting_staged or [])
        if preexisting_staged:
            result["git_commit"] = (
                "Auto-commit skipped: staged user work existed before this task: "
                + ", ".join(preexisting_staged[:10])
                + (" …" if len(preexisting_staged) > 10 else "")
            )
            return

        conflict_sensitive = self.executor.transactions.auto_commit_conflicts()
        if conflict_sensitive:
            result["git_commit"] = (
                "Auto-commit skipped: Sable touched paths that were already dirty at task start: "
                + ", ".join(conflict_sensitive[:10])
                + (" …" if len(conflict_sensitive) > 10 else "")
            )
            return

        # Catch staged work that appeared during the run before Sable stages its own paths.
        staged_before_sable = self.executor.git_staged_paths()
        if staged_before_sable:
            result["git_commit"] = (
                "Auto-commit skipped: staged changes appeared during the task before Sable staging: "
                + ", ".join(staged_before_sable[:10])
                + (" …" if len(staged_before_sable) > 10 else "")
            )
            return

        stage = self.executor.git_add_paths(result["changed_files"])
        if not stage.success:
            result["git_commit"] = f"Stage failed: {stage.error}"
            return

        staged = self.executor._git_raw(
            ["diff", "--cached", "--name-only"],
            "git_status",
            cwd=repo_root,
        )
        if not staged.success or not staged.output.strip():
            result["git_commit"] = "Nothing new to commit."
            return

        short_intent = " ".join(user_message.strip().split())[:68]
        message = f"feat: {short_intent}" if short_intent else "feat: update project with Sable"
        self._status("Committing verified Sable changes...")
        commit = self.executor.git_commit(message)
        result["git_commit"] = commit.output if commit.success else f"Commit failed: {commit.error}"
        if not commit.success:
            return
        commit_sha = self.executor.git_head_sha()
        if commit_sha:
            result["commit_sha"] = commit_sha
            self.executor.transactions.set_commit(commit_sha)

        # No surprise publishing. auto_push must be enabled AND yolo mode must be active.
        if self.auto_push and mode == "yolo" and self.executor.git_ahead_count() > 0:
            self._status("Auto-push is enabled; pushing current branch...")
            push = self.executor.git_push()
            result["git_push"] = push.output if push.success else ("__NEEDS_REMOTE__" if push.error == "__NO_REMOTE__" else f"Push failed: {push.error}")
