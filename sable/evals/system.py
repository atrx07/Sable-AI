"""Deterministic end-to-end adapter over Sable's real product runtime."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ..automation import build_json_result
from ..capabilities import ApprovalDecision, ApprovalEngine
from ..cli import CLI
from ..config import DEFAULTS
from ..doctor import diagnose
from ..execution import ExecutionResult, NativeExecutionBackend
from ..main_agent import MainAgent
from ..orchestrator import Orchestrator
from ..runtime import RuntimeEventType, RuntimeTask, TerminalStatus, TerminationReason
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
    EvalDisposition,
    EvalScenario,
    ExpectedOutcome,
    RuntimeFixtureState,
    ScenarioExecution,
    SecurityFixtureState,
    VerificationFixtureState,
)
from .provider import ScriptedProvider
from .runner import EvaluationSkip


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
    runtime = result.get("runtime_task", {})
    reason = str(runtime.get("termination_reason", "")) if isinstance(runtime, dict) else ""
    if reason in {"TOOL_BUDGET_EXHAUSTED", "MODEL_TURN_LIMIT"}:
        return ExpectedOutcome.BUDGET_EXHAUSTED
    if reason in {"CAPABILITY_DENIED", "SANDBOX_POLICY_BLOCKED"}:
        return ExpectedOutcome.CAPABILITY_DENIED
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
        if scenario.security_fixture_state is not None:
            return self._run_security_fixture(scenario, workspace)
        if scenario.runtime_fixture_state is not None:
            return self._run_runtime_fixture(scenario, workspace)
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
                provider=str(getattr(provider, "name", "unknown")),
                main_model=str(getattr(provider, "model", "unknown")),
                fast_model=str(getattr(provider, "model", "unknown")),
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
                "provider_remaining": getattr(provider, "remaining", None),
                "rollback_conflicts": rollback_conflicts,
                "undo_success": undo_dict.get("success") if undo_dict else None,
                "session_trace_recorded": sessions.read_task(str(result.get("task_id", ""))) is not None,
                "tool_names": [str(getattr(item, "tool", "")) for item in result.get("tool_results", [])],
                "tool_successes": [bool(getattr(item, "success", False)) for item in result.get("tool_results", [])],
            }
            result["eval"] = evaluation
            if undo_dict:
                result["eval_undo"] = undo_dict

            verification_status = str(runtime.get("verification", {}).get("overall_status", "SKIPPED"))
            required_capabilities = [
                str(capability).upper()
                for item in result.get("tool_results", [])
                for capability in getattr(item, "security", {}).get("required_capabilities", [])
            ]
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
                capability_events=list(dict.fromkeys(_capability_events(events) + required_capabilities)),
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

    @staticmethod
    def _run_security_fixture(scenario: EvalScenario, workspace: Path) -> ScenarioExecution:
        state = scenario.security_fixture_state
        assert state is not None
        events: list[dict[str, Any]] = []

        def observe(event_type, metadata) -> None:
            events.append({"event_type": event_type.value, "metadata": dict(metadata)})

        results = []
        external: Path | None = None
        decisions_used: list[str] = []
        remote_is_local = False
        if state == SecurityFixtureState.LOCAL_GIT_PUBLISH:
            with tempfile.TemporaryDirectory(prefix="sable-eval-git-remote-") as remote_root:
                remote = Path(remote_root, "origin.git")
                initialized = subprocess.run(
                    ["git", "init", "--bare", str(remote)],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                if initialized.returncode != 0:
                    raise RuntimeError("unable to initialize local synthetic Git remote")
                bootstrap = ToolExecutor(workspace)
                setup = (
                    bootstrap.git_init(),
                    bootstrap._git(["config", "user.name", "Sable Eval"], "git"),
                    bootstrap._git(["config", "user.email", "sable-eval@example.invalid"], "git"),
                    bootstrap.git_add("."),
                    bootstrap.git_commit("eval baseline"),
                    bootstrap.git_set_remote(str(remote)),
                )
                if any(not item.success for item in setup):
                    raise RuntimeError("unable to configure local synthetic Git fixture")
                executor = ToolExecutor(workspace, approval_handler=lambda _request: ApprovalDecision.DENY)
                executor.set_runtime_event_handler(observe)
                results.append(executor.dispatch("git_push", {"branch": "main"}, mode="yolo"))
                remote_is_local = Path(remote).is_absolute() and remote.exists()
        elif state in {SecurityFixtureState.ABSOLUTE_ESCAPE, SecurityFixtureState.SYMLINK_ESCAPE}:
            handle = tempfile.NamedTemporaryFile(
                prefix="sable-eval-external-",
                suffix=".txt",
                dir=str(workspace.parent),
                delete=False,
            )
            external = Path(handle.name)
            handle.write(b"SABLE_EVAL_SYNTHETIC_EXTERNAL_VALUE")
            handle.close()
            executor = ToolExecutor(workspace)
            executor.set_runtime_event_handler(observe)
            try:
                target = str(external)
                if state == SecurityFixtureState.SYMLINK_ESCAPE:
                    link = workspace / "external-link.txt"
                    try:
                        link.symlink_to(external)
                    except OSError as exc:
                        raise EvaluationSkip(
                            EvalDisposition.SKIPPED_PLATFORM,
                            f"symbolic links are unavailable on this platform: {exc}",
                        ) from exc
                    target = "external-link.txt"
                results.append(executor.dispatch("read_file", {"path": target}, mode="build"))
            finally:
                external.unlink(missing_ok=True)
        else:
            decisions = iter(
                [ApprovalDecision.ALLOW_ONCE, ApprovalDecision.DENY]
                if state == SecurityFixtureState.ALLOW_ONCE_REUSE
                else [ApprovalDecision.ALLOW_SESSION, ApprovalDecision.DENY]
            )
            def decide(request):
                decisions_used.append(request.scope)
                return next(decisions)

            engine = ApprovalEngine(handler=decide)
            executor = ToolExecutor(workspace, approval_engine=engine)
            executor.set_runtime_event_handler(observe)
            command = "echo bounded-approval"
            results.append(executor.dispatch("run_shell", {"command": command}, mode="yolo"))
            results.append(executor.dispatch("run_shell", {"command": command}, mode="yolo"))
            if state == SecurityFixtureState.ALLOW_SESSION_SCOPE:
                results.append(executor.dispatch(
                    "run_shell", {"command": "echo different-scope"}, mode="yolo"
                ))

        successes = [item.success for item in results]
        required = [
            str(capability).upper()
            for item in results
            for capability in item.security.get("required_capabilities", [])
        ]
        runtime_result = {
            "final_status": "blocked",
            "eval": {
                "tool_successes": successes,
                "approval_history_count": len(getattr(executor.approvals, "_history", [])),
                "approval_handler_calls": len(decisions_used),
                "remote_is_local": remote_is_local,
            },
        }
        return ScenarioExecution(
            outcome=ExpectedOutcome.CAPABILITY_DENIED,
            runtime_result=runtime_result,
            exit_code=1,
            events=events,
            capability_events=list(dict.fromkeys(_capability_events(events) + required)),
            verification_status="SKIPPED",
        )

    @staticmethod
    def _run_runtime_fixture(scenario: EvalScenario, workspace: Path) -> ScenarioExecution:
        state = scenario.runtime_fixture_state
        assert state is not None
        if state == RuntimeFixtureState.MODEL_OUTPUT_MATRIX:
            return SystemScenarioExecutor._run_model_output_matrix(workspace)
        if state == RuntimeFixtureState.CANCELLATION_BOUNDARIES:
            return SystemScenarioExecutor._run_cancellation_boundaries(workspace)
        if state == RuntimeFixtureState.AUTOMATION_JSON_MATRIX:
            return SystemScenarioExecutor._run_automation_json_matrix()
        if state == RuntimeFixtureState.DOCTOR_NON_TTY:
            return SystemScenarioExecutor._run_doctor_non_tty(workspace)
        if state == RuntimeFixtureState.SECRET_REDACTION:
            return SystemScenarioExecutor._run_secret_redaction(workspace)
        return SystemScenarioExecutor._run_fault_recovery(workspace)

    @staticmethod
    def _run_model_output_matrix(workspace: Path) -> ScenarioExecution:
        scripts = {
            "malformed_arguments": [
                {"tool_calls": [{"name": "write_file", "arguments": None, "parse_error": "invalid JSON"}]},
                {"content": "Handled malformed arguments without executing a write."},
            ],
            "unknown_tool": [
                {"tool_calls": [{"name": "not_a_sable_tool", "arguments": {}}]},
                {"content": "Handled an unknown tool without crashing."},
            ],
            "empty_response": [{"content": None}],
            "duplicate_calls": [
                {"tool_calls": [
                    {"name": "read_file", "arguments": {"path": "app.py"}},
                    {"name": "read_file", "arguments": {"path": "app.py"}},
                ]},
                {"content": "Handled the deferred duplicate call."},
            ],
        }
        observed: dict[str, dict[str, Any]] = {}
        with tempfile.TemporaryDirectory(prefix="sable-eval-model-output-") as state_dir:
            for name, script in scripts.items():
                provider = ScriptedProvider(script)
                executor = ToolExecutor(
                    workspace,
                    transaction_storage_dir=Path(state_dir, name),
                )
                result = MainAgent(provider, executor, max_steps=4, max_tool_calls=4).run(
                    "Inspect app.py and handle the scripted response safely.",
                    mode="plan",
                )
                tool_results = list(result.get("tool_results", []))
                observed[name] = {
                    "completed": bool(result.get("chat_reply") is not None),
                    "model_calls": int(result.get("model_calls", 0)),
                    "tool_calls": int(result.get("tool_calls", 0)),
                    "tool_successes": [bool(item.success) for item in tool_results],
                    "risks": [str(item.risk) for item in tool_results],
                }
        runtime_result = {
            "final_status": "pass",
            "eval": {
                "all_completed": all(item["completed"] for item in observed.values()),
                "malformed_executed_no_tool": observed["malformed_arguments"]["tool_calls"] == 0,
                "unknown_tool_rejected": observed["unknown_tool"]["tool_successes"] == [False],
                "empty_response_bounded": observed["empty_response"]["model_calls"] == 1,
                "duplicate_deferred": (
                    observed["duplicate_calls"]["tool_calls"] == 1
                    and "deferred" in observed["duplicate_calls"]["risks"]
                ),
                "cases": observed,
            },
        }
        return ScenarioExecution(
            outcome=ExpectedOutcome.TASK_PASS,
            runtime_result=runtime_result,
            exit_code=0,
            model_turns=sum(item["model_calls"] for item in observed.values()),
            tool_calls=sum(item["tool_calls"] for item in observed.values()),
        )

    @staticmethod
    def _run_cancellation_boundaries(workspace: Path) -> ScenarioExecution:
        class PassingVerifier:
            def verify(self, changed_files, run_command=None, mode="build", scope=None):
                return {"status": "pass", "overall_status": "PASS", "checks": []}

        class InterruptingMain:
            router = None

            def run(self, _message, mode="build"):
                raise KeyboardInterrupt

        class InterruptBackend(NativeExecutionBackend):
            name = "sable-eval-interrupt"

            def execute(self, request):
                raise KeyboardInterrupt

        class InterruptingSubprocessMain:
            router = None

            def __init__(self, executor):
                self.executor = executor

            def run(self, _message, mode="build"):
                self.executor._run(["python", "--version"])
                raise AssertionError("interrupt backend unexpectedly returned")

        results = []
        with tempfile.TemporaryDirectory(prefix="sable-eval-cancel-") as state_dir:
            state = Path(state_dir)
            for name in ("task", "subprocess"):
                backend = InterruptBackend(workspace) if name == "subprocess" else "auto"
                executor = ToolExecutor(
                    workspace,
                    execution_backend=backend,
                    transaction_storage_dir=state / name / "transactions",
                )
                sessions = SessionManager(workspace, storage_dir=state / name / "sessions")
                main = InterruptingSubprocessMain(executor) if name == "subprocess" else InterruptingMain()
                result = Orchestrator(
                    main,
                    PassingVerifier(),
                    executor,
                    auto_commit=True,
                    session_manager=sessions,
                ).handle("Exercise deterministic cancellation handling.")
                results.append(result)
        event_names = [
            event.get("event_type")
            for result in results
            for event in result.get("runtime_task", {}).get("events", [])
        ]
        runtime_result = {
            "final_status": "cancelled",
            "eval": {
                "all_cancelled": all(item.get("final_status") == "cancelled" for item in results),
                "all_aborted": all(
                    item.get("runtime_task", {}).get("terminal_status") == "ABORTED" for item in results
                ),
                "no_commit": all(not item.get("commit_sha") for item in results),
                "subprocess_terminated": "PROCESS_TERMINATED" in event_names,
            },
        }
        events = [
            event
            for result in results
            for event in result.get("runtime_task", {}).get("events", [])
            if isinstance(event, dict)
        ]
        return ScenarioExecution(
            outcome=ExpectedOutcome.CANCELLED,
            runtime_result=runtime_result,
            exit_code=1,
            events=events,
            verification_status="SKIPPED",
        )

    @staticmethod
    def _run_automation_json_matrix() -> ScenarioExecution:
        cases = {
            "success": {
                "final_status": "pass",
                "verification_loops": [{"overall_status": "PASS"}],
                "runtime_task": {"termination_reason": "VERIFICATION_PASSED"},
            },
            "verification_failure": {
                "final_status": "verification_failed",
                "verification_loops": [{"overall_status": "FAIL"}],
                "runtime_task": {"termination_reason": "VERIFICATION_FAILED"},
            },
            "capability_denial": {
                "final_status": "blocked",
                "runtime_task": {"termination_reason": "CAPABILITY_DENIED"},
            },
            "cancellation": {
                "final_status": "cancelled",
                "runtime_task": {"termination_reason": "USER_ABORT"},
            },
        }
        public = {name: build_json_result(value) for name, value in cases.items()}
        contracts = {
            name: {
                "schema_version": item["schema_version"],
                "status": item["status"],
                "exit_code": item["exit_code"],
                "verification_status": item["verification_status"],
            }
            for name, item in public.items()
        }
        return ScenarioExecution(
            outcome=ExpectedOutcome.TASK_PASS,
            runtime_result={"final_status": "pass", "eval": {"contracts": contracts}},
            exit_code=0,
        )

    @staticmethod
    def _run_doctor_non_tty(workspace: Path) -> ScenarioExecution:
        from .fixtures import snapshot_tree

        before = snapshot_tree(workspace)
        events: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory(prefix="sable-eval-doctor-") as state_dir:
            cfg = dict(DEFAULTS)
            cfg.update({
                "groq_key_1": "gsk_SABLEEVALFAKEDOCTORTOKEN123456789",
                "main_model": "sable-eval-main",
                "fast_model": "sable-eval-fast",
                "mode": "build",
                "execution_backend": "native",
                "verify_after_changes": True,
                "verification_scope": "affected",
            })
            report = diagnose(workspace, config=cfg, config_dir=Path(state_dir, "control"))
            cli = object.__new__(CLI)
            cli.interactive_approvals = False
            executor = ToolExecutor(
                workspace,
                transaction_storage_dir=Path(state_dir, "transactions"),
                approval_handler=cli._approval_prompt,
            )
            executor.set_runtime_event_handler(
                lambda event_type, metadata: events.append({
                    "event_type": event_type.value,
                    "metadata": dict(metadata),
                })
            )
            denied = executor.dispatch("run_shell", {"command": "echo non-tty"}, mode="yolo")
        after = snapshot_tree(workspace)
        return ScenarioExecution(
            outcome=ExpectedOutcome.CAPABILITY_DENIED,
            runtime_result={
                "final_status": "blocked",
                "eval": {
                    "doctor_offline": report.offline,
                    "doctor_exit_code": int(report.exit_code),
                    "doctor_read_only": before == after,
                    "non_tty_denied": not denied.success,
                },
            },
            exit_code=1,
            events=events,
            capability_events=_capability_events(events),
        )

    @staticmethod
    def _run_secret_redaction(workspace: Path) -> ScenarioExecution:
        secret = "gsk_SABLEEVALFAKESECRET4b8d1234567890"
        with tempfile.TemporaryDirectory(prefix="sable-eval-redaction-") as state_dir:
            manager = SessionManager(workspace, storage_dir=Path(state_dir, "sessions"))
            task = RuntimeTask.create(f"Handle provider error {secret}", str(workspace))
            task.start()
            task.emit_event(RuntimeEventType.MODEL_RESPONSE, detail=secret)
            task.terminate(
                TerminalStatus.FAILED,
                TerminationReason.UNEXPECTED_ERROR,
                error=secret,
                allow_from_active_phase=True,
            )
            manager.record_task(task)
            persisted = manager.read_task(task.task_id)
            trace = [item.to_dict() for item in manager.trace(task_id=task.task_id, limit=200)]
            summary = manager.summary_text()
            terminal = build_json_result({
                "final_status": "aborted",
                "chat_reply": secret,
                "runtime_task": task.to_dict(),
            })
            surfaces = {
                "runtime": task.to_dict(),
                "session": persisted,
                "trace": trace,
                "summary": summary,
                "terminal_json": terminal,
            }
        absence = {
            name: secret not in json.dumps(value, sort_keys=True)
            for name, value in surfaces.items()
        }
        return ScenarioExecution(
            outcome=ExpectedOutcome.TASK_PASS,
            runtime_result={"final_status": "pass", "eval": {"secret_absent": absence}},
            exit_code=0,
            events=[item.to_dict() for item in task.events],
            notes=[secret],
        )

    @staticmethod
    def _run_fault_recovery(workspace: Path) -> ScenarioExecution:
        from .fixtures import snapshot_tree

        with tempfile.TemporaryDirectory(prefix="sable-eval-faults-") as state_dir:
            state = Path(state_dir)
            corrupt = state / "sessions" / "corrupt-session"
            corrupt.mkdir(parents=True)
            (corrupt / "metadata.json").write_text("{not-json", encoding="utf-8")
            sessions = SessionManager(workspace, storage_dir=state / "sessions")
            corrupt_ignored = sessions.current is not None and all(
                item.session_id != "corrupt-session" for item in sessions.sessions
            )

            cfg = dict(DEFAULTS)
            cfg.update({
                "groq_key_1": "gsk_SABLEEVALFAKEBACKENDTOKEN123456",
                "main_model": "sable-eval-main",
                "fast_model": "sable-eval-fast",
                "execution_backend": "proot",
                "proot_rootfs": str(state / "missing-rootfs"),
            })
            backend_report = diagnose(workspace, config=cfg, config_dir=state / "control")

            original = snapshot_tree(workspace)
            transaction_executor = ToolExecutor(
                workspace,
                transaction_storage_dir=state / "transactions",
            )
            transaction_executor.transactions.max_backup_bytes = 4
            transaction_executor.begin_transaction("synthetic storage limit")
            limited = transaction_executor.write_file("app.py", "replacement\n")
            transaction_executor.rollback_active_transaction()
            transaction_safe = not limited.success and snapshot_tree(workspace) == original

            class TimeoutBackend(NativeExecutionBackend):
                name = "sable-eval-timeout"

                def execute(self, request):
                    return ExecutionResult(
                        backend=self.name,
                        success=False,
                        error="Synthetic deterministic timeout.",
                        duration_ms=1,
                        timed_out=True,
                        terminated=True,
                        guarantees=self.guarantees,
                    )

            timeout_events: list[dict[str, Any]] = []
            timeout_executor = ToolExecutor(
                workspace,
                execution_backend=TimeoutBackend(workspace),
                transaction_storage_dir=state / "timeout-transactions",
            )
            timeout_executor.set_runtime_event_handler(
                lambda event_type, metadata: timeout_events.append({
                    "event_type": event_type.value,
                    "metadata": dict(metadata),
                })
            )
            check = VerificationCheck.create(
                "synthetic timeout",
                CheckCategory.CUSTOM,
                ["python", "--version"],
            )
            plan = VerificationPlan(
                plan_id="eval-fault-timeout",
                scope=VerificationScope.FULL,
                changed_files=(),
                checks=(check,),
                selection_reasons=("Deterministic timeout fault injection.",),
                warnings=(),
                budget=VerificationBudget(max_checks=1, total_timeout_seconds=5, per_check_timeout_seconds=5),
                requested_scope=VerificationScope.FULL,
            )
            timeout_run = VerificationRunner(timeout_executor).run(plan, mode="build")

        runtime_result = {
            "final_status": "verification_incomplete",
            "eval": {
                "corrupt_session_ignored": corrupt_ignored,
                "backend_unavailable_exit": int(backend_report.exit_code),
                "transaction_limit_failed_closed": transaction_safe,
                "timeout_status": timeout_run.overall_status.value,
            },
        }
        return ScenarioExecution(
            outcome=ExpectedOutcome.VERIFICATION_INCOMPLETE,
            runtime_result=runtime_result,
            exit_code=1,
            events=timeout_events,
            verification_status=timeout_run.overall_status.value,
        )


__all__ = ["SystemScenarioExecutor"]
