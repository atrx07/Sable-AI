"""Deterministic end-to-end adapter over Sable's real product runtime."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from ..capabilities import ApprovalDecision
from ..main_agent import MainAgent
from ..orchestrator import Orchestrator
from ..sessions import SessionManager
from ..tools import ToolExecutor
from ..verification import (
    CheckAvailability,
    CheckCategory,
    VerificationBudget,
    VerificationCheck,
    VerificationPlan,
    VerificationRunner,
    VerificationScope,
)
from ..verifier import Verifier
from .models import (
    EvalFileWrite,
    EvalScenario,
    ExpectedOutcome,
    ScenarioExecution,
    VerificationFixtureState,
)
from .provider import ScriptedProvider


def _write_user_file(workspace: Path, write: EvalFileWrite) -> None:
    target = (workspace / write.path).resolve()
    try:
        target.relative_to(workspace.resolve())
    except ValueError as exc:
        raise ValueError(f"eval write escapes workspace: {write.path}") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(write.content, encoding="utf-8")


def _check_value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _verification_metrics(result: dict[str, Any]) -> dict[str, Any]:
    stages: list[str] = []
    scopes: list[str] = []
    classifications: list[str] = []
    affected_targets: list[str] = []
    scope_escalated = False
    for run in result.get("verification_loops", []):
        if not isinstance(run, dict):
            continue
        stages.append(str(run.get("stage", "")))
        scopes.append(str(run.get("scope", "")))
        plan = run.get("plan", {})
        if isinstance(plan, dict):
            scope_escalated = scope_escalated or bool(plan.get("scope_escalated", False))
            for check in plan.get("checks", []):
                if isinstance(check, dict):
                    affected_targets.extend(str(path) for path in check.get("affected_files", []))
                    affected_targets.extend(
                        str(arg).replace("\\", "/")
                        for arg in check.get("argv", [])
                        if str(arg).replace("\\", "/").startswith("tests/")
                    )
        for check_result in run.get("checks", []):
            classification = _check_value(check_result, "classification", "")
            classifications.append(str(getattr(classification, "value", classification)))
            check = _check_value(check_result, "check")
            affected_targets.extend(str(path) for path in _check_value(check, "affected_files", ()) or ())
    return {
        "verification_stages": stages,
        "verification_scopes": scopes,
        "failure_classifications": list(dict.fromkeys(item for item in classifications if item and item != "NONE")),
        "affected_test_targets": list(dict.fromkeys(affected_targets)),
        "scope_escalated": scope_escalated,
    }


def _context_metrics(scenario: EvalScenario, runtime: dict[str, Any]) -> tuple[dict[str, float | int], list[str]]:
    context = runtime.get("context_selection", {})
    if not isinstance(context, dict):
        context = {}
    selected = [
        str(item.get("path", "")).replace("\\", "/")
        for item in context.get("items", [])
        if isinstance(item, dict) and item.get("path")
    ]
    required = {path.replace("\\", "/") for path in scenario.required_context_files}
    selected_set = set(selected)
    relevant_selected = len(required & selected_set)
    recall = relevant_selected / len(required) if required else 1.0
    precision = relevant_selected / len(selected_set) if selected_set else (1.0 if not required else 0.0)
    return ({
        "repository_files_considered": int(context.get("files_considered", 0) or 0),
        "context_files_selected": int(context.get("files_selected", len(selected)) or 0),
        "context_characters_used": int(context.get("characters_used", 0) or 0),
        "required_context_files": len(required),
        "required_context_files_selected": relevant_selected,
        "fixture_context_recall": round(recall, 4),
        "fixture_context_precision": round(precision, 4),
    }, selected)


def _capability_events(events: list[dict[str, Any]]) -> list[str]:
    capabilities: list[str] = []
    for event in events:
        metadata = event.get("metadata", {}) if isinstance(event, dict) else {}
        if not isinstance(metadata, dict):
            continue
        for value in metadata.get("required_capabilities", []) or []:
            capabilities.append(str(value).upper())
        capability = metadata.get("capability")
        if capability:
            capabilities.append(str(capability).upper())
    return list(dict.fromkeys(capabilities))


def _outcome(result: dict[str, Any], *, rollback_conflicts: list[str], undo_requested: bool) -> ExpectedOutcome:
    if undo_requested:
        return ExpectedOutcome.ROLLBACK_CONFLICT if rollback_conflicts else ExpectedOutcome.ROLLBACK_SUCCESS
    status = str(result.get("final_status", ""))
    if status in {"pass", "built"}:
        return ExpectedOutcome.TASK_PASS
    if status == "verification_incomplete":
        return ExpectedOutcome.VERIFICATION_INCOMPLETE
    if status.startswith("verification_") or status == "repair_no_progress":
        return ExpectedOutcome.VERIFICATION_FAIL
    if status == "cancelled":
        return ExpectedOutcome.CANCELLED
    return ExpectedOutcome.TASK_FAIL


class SystemScenarioExecutor:
    """Run a scenario through the same agent, tools, verification, and trace path as users."""

    def __call__(
        self,
        scenario: EvalScenario,
        workspace: Path,
        provider: ScriptedProvider,
    ) -> ScenarioExecution:
        if scenario.verification_fixture_state is not None:
            return self._run_verification_fixture(scenario, workspace)
        with tempfile.TemporaryDirectory(prefix="sable-eval-state-") as state_dir:
            state = Path(state_dir)
            bootstrap = ToolExecutor(workspace, transaction_storage_dir=state / "transactions")
            if scenario.initialize_git:
                for result in (
                    bootstrap.git_init(),
                    bootstrap._git(["config", "user.name", "Sable Eval"], "git"),
                    bootstrap._git(["config", "user.email", "sable-eval@example.invalid"], "git"),
                    bootstrap.git_add("."),
                    bootstrap.git_commit("eval baseline"),
                ):
                    if not result.success:
                        raise RuntimeError(f"unable to initialize eval git fixture: {result.error}")
            for write in scenario.pre_run_writes:
                _write_user_file(workspace, write)

            approved = {item.upper() for item in scenario.approved_capabilities}
            executor = ToolExecutor(
                workspace,
                transaction_storage_dir=state / "transactions",
                approval_handler=lambda request: (
                    ApprovalDecision.ALLOW_ONCE
                    if request.capability.value in approved
                    else ApprovalDecision.DENY
                ),
            )
            sessions = SessionManager(
                workspace,
                provider="scripted",
                main_model=provider.model,
                fast_model=provider.model,
                storage_dir=state / "sessions",
            )
            main = MainAgent(
                provider,
                executor,
                max_steps=scenario.max_model_turns,
                max_tool_calls=scenario.max_tool_calls,
            )
            verifier = Verifier(
                executor,
                budget=VerificationBudget(
                    max_checks=8,
                    total_timeout_seconds=max(1, scenario.max_duration_ms // 1000),
                    per_check_timeout_seconds=max(1, min(30, scenario.max_duration_ms // 1000)),
                    max_repair_cycles=scenario.max_repair_loops,
                ),
            )
            orchestrator = Orchestrator(
                main,
                verifier,
                executor,
                max_fix_loops=scenario.max_repair_loops,
                auto_commit=False,
                auto_push=False,
                verification_scope=scenario.verification_scope,
                session_manager=sessions,
            )
            result = orchestrator.handle(
                scenario.task_prompt,
                mode=scenario.runtime_mode,
                verify_enabled=True,
                run_command=scenario.verification_command,
                verification_scope=scenario.verification_scope,
            )
            for write in scenario.post_run_writes:
                _write_user_file(workspace, write)

            undo_dict: dict[str, Any] = {}
            rollback_conflicts: list[str] = []
            if scenario.undo_after_run:
                undo = executor.undo_transaction(result.get("transaction_id"))
                undo_dict = undo.to_dict()
                transaction = executor.transactions.get(str(result.get("transaction_id", "")))
                if transaction and isinstance(transaction.rollback_outcome, dict):
                    rollback_conflicts = list(transaction.rollback_outcome.get("conflicts", []))
            transaction = executor.transactions.get(str(result.get("transaction_id", "")))

            runtime = result.get("runtime_task", {})
            if not isinstance(runtime, dict):
                runtime = {}
            events = [item for item in runtime.get("events", []) if isinstance(item, dict)]
            context_metrics, selected_context = _context_metrics(scenario, runtime)
            evaluation = {
                **_verification_metrics(result),
                **context_metrics,
                "selected_context_files": selected_context,
                "provider_remaining": provider.remaining,
                "rollback_conflicts": rollback_conflicts,
                "undo_success": undo_dict.get("success") if undo_dict else None,
                "session_trace_recorded": sessions.read_task(str(result.get("task_id", ""))) is not None,
            }
            result["eval"] = evaluation
            if undo_dict:
                result["eval_undo"] = undo_dict

            verification_status = str(runtime.get("verification", {}).get("overall_status", "SKIPPED"))
            successful = str(result.get("final_status", "")) in {"pass", "built"}
            if scenario.undo_after_run:
                successful = bool(undo_dict.get("success"))
            return ScenarioExecution(
                outcome=_outcome(result, rollback_conflicts=rollback_conflicts, undo_requested=scenario.undo_after_run),
                runtime_result=result,
                exit_code=0 if successful else 1,
                changed_files=list(result.get("changed_files", [])),
                events=events,
                verified=verification_status in {"PASS", "PASS_WITH_OPTIONAL_SKIPS"},
                verification_status=verification_status,
                transaction_status=(transaction.status if transaction else None),
                rollback_status=(transaction.rollback_status if transaction else None),
                capability_events=_capability_events(events),
                token_usage={
                    "input_tokens": int(runtime.get("input_tokens", 0) or 0),
                    "output_tokens": int(runtime.get("output_tokens", 0) or 0),
                    "total_tokens": int(runtime.get("total_tokens", 0) or 0),
                },
                context_metrics=context_metrics,
                model_turns=int(runtime.get("model_turn_count", 0) or 0),
                tool_calls=int(runtime.get("tool_call_count", 0) or 0),
                repair_loops=int(runtime.get("repair_loop_count", 0) or 0),
                errors=[] if not scenario.undo_after_run or undo_dict.get("success") else [str(undo_dict.get("error", "undo failed"))],
            )

    @staticmethod
    def _run_verification_fixture(scenario: EvalScenario, workspace: Path) -> ScenarioExecution:
        state = scenario.verification_fixture_state
        assert state is not None
        events: list[dict[str, Any]] = []
        executor = ToolExecutor(workspace)
        executor.set_runtime_event_handler(
            lambda event_type, metadata: events.append({
                "event_type": event_type.value,
                "metadata": dict(metadata),
            })
        )
        passing = VerificationCheck.create(
            "deterministic pass",
            CheckCategory.CUSTOM,
            ["python", "--version"],
        )
        if state == VerificationFixtureState.PASS_WITH_OPTIONAL_SKIPS:
            checks = (passing, VerificationCheck.create(
                "optional unavailable tool",
                CheckCategory.LINT,
                ["sable-eval-optional-tool"],
                required=False,
                availability=CheckAvailability.UNAVAILABLE,
                availability_reason="Synthetic optional tool is intentionally unavailable.",
            ))
        elif state == VerificationFixtureState.INCOMPLETE:
            checks = (VerificationCheck.create(
                "required unavailable tool",
                CheckCategory.TYPECHECK,
                ["sable-eval-required-tool"],
                availability=CheckAvailability.UNAVAILABLE,
                availability_reason="Synthetic required tool is intentionally unavailable.",
            ),)
        elif state == VerificationFixtureState.BLOCKED:
            checks = (VerificationCheck.create(
                "policy-blocked package action",
                CheckCategory.CUSTOM,
                ["python", "-m", "pip", "install", "SABLE_EVAL_NEVER_INSTALL"],
            ),)
        else:
            checks = (VerificationCheck.create(
                "deterministic type check",
                CheckCategory.TYPECHECK,
                ["python", "typecheck.py"],
            ),)
        plan = VerificationPlan(
            plan_id=f"eval-{scenario.scenario_id}",
            scope=VerificationScope.FULL,
            changed_files=(),
            checks=checks,
            selection_reasons=("Deterministic M7 verification-state fixture.",),
            warnings=(),
            budget=VerificationBudget(max_checks=4, total_timeout_seconds=10, per_check_timeout_seconds=5),
            requested_scope=VerificationScope.FULL,
        )
        run = VerificationRunner(executor).run(plan, mode="build")
        status = run.overall_status.value
        if status in {"PASS", "PASS_WITH_OPTIONAL_SKIPS"}:
            outcome = ExpectedOutcome.TASK_PASS
        elif status == "INCOMPLETE":
            outcome = ExpectedOutcome.VERIFICATION_INCOMPLETE
        else:
            outcome = ExpectedOutcome.VERIFICATION_FAIL
        checks_result = [item.to_dict() for item in run.results]
        runtime_result = {
            "final_status": status.lower(),
            "verification": run.to_result_dict(),
            "eval": {
                "check_statuses": [item["status"] for item in checks_result],
                "failure_classifications": [
                    item["classification"] for item in checks_result if item["classification"] != "NONE"
                ],
            },
        }
        return ScenarioExecution(
            outcome=outcome,
            runtime_result=runtime_result,
            exit_code=0 if outcome == ExpectedOutcome.TASK_PASS else 1,
            events=events,
            verified=outcome == ExpectedOutcome.TASK_PASS,
            verification_status=status,
        )


__all__ = ["SystemScenarioExecutor"]
