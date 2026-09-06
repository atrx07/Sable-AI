import tempfile
import unittest
from pathlib import Path

from sable.orchestrator import Orchestrator
from sable.runtime import (
    RuntimeEventType,
    RuntimePhase,
    RuntimeStateError,
    RuntimeTask,
    TerminalStatus,
    TerminationReason,
    request_needs_plan,
)
from sable.sessions import SessionManager
from sable.tools import ToolExecutor, ToolResult


class RuntimeMain:
    def __init__(self, executor, *, result=None, crash=False):
        self.executor = executor
        self.result = result or {}
        self.crash = crash

    def run(self, _message, mode="build"):
        if self.crash:
            self.executor.write_file("changed.txt", "partial")
            raise RuntimeError("runtime crash")
        changed = []
        tool_results = list(self.result.get("tool_results", []))
        if self.result.get("write"):
            write = self.executor.write_file("changed.txt", "updated")
            changed = write.changed_files
            tool_results.append(write)
        return {
            "chat_reply": "done",
            "changes_summary": [],
            "tool_results": tool_results,
            "changed_files": changed,
            "steps": 1,
            "model_calls": 1,
            "tool_calls": len(tool_results),
            "step_limit_reached": self.result.get("step_limit_reached", False),
            "tool_limit_reached": self.result.get("tool_limit_reached", False),
        }


class RuntimeVerifier:
    def __init__(self, status="pass"):
        self.status = status

    def verify(self, changed_files, run_command=None, mode="build"):
        return {"status": self.status, "summary": f"runtime {self.status}", "checks": []}


class TraceRuntimeMain(RuntimeMain):
    def __init__(self, executor):
        super().__init__(executor)
        self.on_event = None

    def run(self, message, mode="build"):
        if self.on_event:
            self.on_event(RuntimeEventType.MODEL_REQUEST, {"provider": "test", "api_key": "gsk_abcdefghijklmnopqrstuvwxyz"})
        result = super().run(message, mode=mode)
        if self.on_event:
            self.on_event(RuntimeEventType.MODEL_RESPONSE, {"provider": "test", "latency_ms": 1})
        return result


class RuntimeStateTests(unittest.TestCase):
    def make_task(self):
        return RuntimeTask.create("change the parser", tempfile.gettempdir())

    def test_valid_successful_lifecycle_emits_transitions(self):
        task = self.make_task()
        task.start()
        for phase in (
            RuntimePhase.CONTEXT,
            RuntimePhase.PLAN,
            RuntimePhase.EXECUTE,
            RuntimePhase.VERIFY,
            RuntimePhase.REPORT,
        ):
            task.transition(phase, reason="test")
        task.terminate(TerminalStatus.COMPLETED, TerminationReason.SUCCESS)

        self.assertEqual(task.terminal_status, TerminalStatus.COMPLETED)
        transitions = [event for event in task.events if event.event_type.value == "PHASE_CHANGED"]
        self.assertEqual([event.metadata["to"] for event in transitions], [
            "DISCOVER", "CONTEXT", "PLAN", "EXECUTE", "VERIFY", "REPORT",
        ])

    def test_invalid_transition_is_rejected(self):
        task = self.make_task()
        with self.assertRaises(RuntimeStateError):
            task.transition(RuntimePhase.VERIFY, reason="invalid")

    def test_repair_lifecycle(self):
        task = self.make_task()
        task.start()
        task.transition(RuntimePhase.CONTEXT, reason="ready")
        task.transition(RuntimePhase.EXECUTE, reason="simple")
        task.transition(RuntimePhase.VERIFY, reason="edited")
        task.transition(RuntimePhase.REPAIR, reason="failed")
        task.transition(RuntimePhase.VERIFY, reason="repaired")
        task.transition(RuntimePhase.REPORT, reason="verified")
        task.terminate(TerminalStatus.COMPLETED, TerminationReason.SUCCESS)
        self.assertEqual(task.current_phase, RuntimePhase.REPORT)

    def test_terminal_state_is_immutable(self):
        task = self.make_task()
        task.start()
        task.transition(RuntimePhase.CONTEXT, reason="ready")
        task.transition(RuntimePhase.REPORT, reason="done")
        task.terminate(TerminalStatus.BLOCKED, TerminationReason.POLICY_BLOCKED)
        with self.assertRaises(RuntimeStateError):
            task.transition(RuntimePhase.EXECUTE, reason="too late")
        with self.assertRaises(RuntimeStateError):
            task.terminate(TerminalStatus.COMPLETED, TerminationReason.SUCCESS)

    def test_active_phase_abort_is_explicit(self):
        task = self.make_task()
        task.start()
        task.terminate(
            TerminalStatus.ABORTED,
            TerminationReason.UNEXPECTED_ERROR,
            error="boom",
            allow_from_active_phase=True,
        )
        self.assertEqual(task.termination_reason, TerminationReason.UNEXPECTED_ERROR)
        self.assertEqual(task.errors, ["boom"])

    def test_trivial_inspection_can_skip_plan(self):
        self.assertFalse(request_needs_plan("list files"))
        self.assertFalse(request_needs_plan("inspect git status"))
        self.assertTrue(request_needs_plan("refactor the parser"))


class OrchestratorRuntimeTests(unittest.TestCase):
    def make_orchestrator(self, root, store, *, main_result=None, verifier="pass", crash=False):
        executor = ToolExecutor(root, transaction_storage_dir=store)
        Path(root, "changed.txt").write_text("before")
        main = RuntimeMain(executor, result=main_result, crash=crash)
        return Orchestrator(main, RuntimeVerifier(verifier), executor, auto_commit=False, max_fix_loops=0)

    def test_successful_run_records_runtime_and_transaction_link(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as store:
            orchestrator = self.make_orchestrator(root, store, main_result={"write": True})
            result = orchestrator.handle("change the file")
            runtime = result["runtime_task"]
            self.assertEqual(runtime["terminal_status"], "COMPLETED")
            self.assertEqual(runtime["termination_reason"], "SUCCESS")
            self.assertEqual(runtime["transaction_id"], result["transaction_id"])
            self.assertEqual(runtime["model_turn_count"], 1)
            self.assertEqual(runtime["tool_call_count"], 1)
            self.assertIn("changed.txt", runtime["changed_files"])

    def test_verification_failure_records_failed_lifecycle(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as store:
            orchestrator = self.make_orchestrator(root, store, main_result={"write": True}, verifier="fail")
            runtime = orchestrator.handle("change the file")["runtime_task"]
            self.assertEqual(runtime["terminal_status"], "FAILED")
            self.assertEqual(runtime["termination_reason"], "VERIFICATION_FAILED")
            phases = [event["metadata"].get("to") for event in runtime["events"]]
            self.assertIn("VERIFY", phases)
            self.assertIn("REPORT", phases)

    def test_policy_blocked_termination_is_structured(self):
        blocked = ToolResult("delete_file", False, error="denied", approval_required=True, risk="high")
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as store:
            orchestrator = self.make_orchestrator(root, store, main_result={"tool_results": [blocked]})
            result = orchestrator.handle("delete a protected file")
            self.assertEqual(result["final_status"], "blocked")
            self.assertEqual(result["runtime_task"]["termination_reason"], "POLICY_BLOCKED")

    def test_tool_and_model_limits_have_distinct_reasons(self):
        cases = (("tool_limit_reached", "TOOL_BUDGET_EXHAUSTED"), ("step_limit_reached", "MODEL_TURN_LIMIT"))
        for flag, reason in cases:
            with self.subTest(flag=flag), tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as store:
                orchestrator = self.make_orchestrator(root, store, main_result={flag: True})
                runtime = orchestrator.handle("perform a long task")["runtime_task"]
                self.assertEqual(runtime["terminal_status"], "BLOCKED")
                self.assertEqual(runtime["termination_reason"], reason)

    def test_unexpected_exception_records_aborted_task(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as store:
            orchestrator = self.make_orchestrator(root, store, crash=True)
            result = orchestrator.handle("crash during execution")
            runtime = result["runtime_task"]
            self.assertEqual(runtime["terminal_status"], "ABORTED")
            self.assertEqual(runtime["termination_reason"], "UNEXPECTED_ERROR")
            self.assertEqual(runtime["transaction_id"], result["transaction_id"])
            self.assertEqual(Path(root, "changed.txt").read_text(), "before")

    def test_session_trace_failure_does_not_fail_task(self):
        class BrokenSession:
            current = None

            def record_task(self, _task):
                raise OSError("trace storage unavailable")

        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as store:
            executor = ToolExecutor(root, transaction_storage_dir=store)
            orchestrator = Orchestrator(
                RuntimeMain(executor), RuntimeVerifier(), executor,
                auto_commit=False, session_manager=BrokenSession(),
            )
            result = orchestrator.handle("inspect repository")
            self.assertEqual(result["final_status"], "built")
            self.assertIn("trace storage unavailable", result["trace_errors"])

    def test_runtime_events_are_persisted_with_task_and_transaction_links(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as store, tempfile.TemporaryDirectory() as session_store:
            executor = ToolExecutor(root, transaction_storage_dir=store)
            sessions = SessionManager(root, storage_dir=session_store)
            orchestrator = Orchestrator(
                TraceRuntimeMain(executor), RuntimeVerifier(), executor,
                auto_commit=False, session_manager=sessions,
            )
            result = orchestrator.handle("inspect repository")
            events = sessions.trace(task_id=result["task_id"], limit=100)
            event_types = [event.event_type for event in events]
            self.assertIn("TASK_STARTED", event_types)
            self.assertIn("TRANSACTION_STARTED", event_types)
            self.assertIn("MODEL_REQUEST", event_types)
            self.assertIn("MODEL_RESPONSE", event_types)
            self.assertEqual(events[-1].event_type, "TASK_COMPLETED")
            self.assertTrue(all(event.transaction_id == result["transaction_id"] for event in events if event.task_id))
            trace_path = Path(session_store) / sessions.current.session_id / "events.jsonl"
            self.assertNotIn("gsk_abcdefghijklmnopqrstuvwxyz", trace_path.read_text(encoding="utf-8"))
