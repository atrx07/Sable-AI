import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sable.execution import EnvironmentPolicy, ExecutionRequest, NativeExecutionBackend
from sable.orchestrator import Orchestrator
from sable.runtime import RuntimeEventType
from sable.sessions import SessionManager
from sable.tools import ToolExecutor


class InterruptingMain:
    router = None

    def __init__(self, executor, *, after_write=False):
        self.executor = executor
        self.after_write = after_write

    def run(self, _message, mode="build"):
        if self.after_write:
            self.executor.write_file("partial.py", "partial = True\n")
        raise KeyboardInterrupt


class WritingMain:
    router = None

    def __init__(self, executor):
        self.executor = executor

    def run(self, _message, mode="build"):
        result = self.executor.write_file("changed.py", "changed = True\n")
        return {
            "chat_reply": "changed", "changes_summary": [], "tool_results": [result],
            "changed_files": result.changed_files, "steps": 1, "tool_calls": 1,
        }


class PassingVerifier:
    def verify(self, changed_files, run_command=None, mode="build", scope=None):
        return {"status": "pass", "overall_status": "PASS", "checks": []}


class InterruptingVerifier:
    def verify(self, changed_files, run_command=None, mode="build", scope=None):
        raise KeyboardInterrupt


class CancellationTests(unittest.TestCase):
    def run_case(self, root, store, sessions, main, verifier):
        executor = ToolExecutor(root, transaction_storage_dir=store)
        manager = SessionManager(root, storage_dir=sessions)
        result = Orchestrator(
            main(executor), verifier, executor,
            auto_commit=True, session_manager=manager,
        ).handle("cancel this task")
        return result, executor, manager

    def assert_cancelled(self, result, executor, manager):
        self.assertEqual(result["final_status"], "cancelled")
        self.assertEqual(result["runtime_task"]["terminal_status"], "ABORTED")
        self.assertEqual(result["runtime_task"]["termination_reason"], "USER_ABORT")
        self.assertNotIn("commit_sha", result)
        transaction = executor.transactions.get(result["transaction_id"])
        self.assertEqual(transaction.status, "FAILED")
        events = manager.trace(task_id=result["task_id"], limit=200)
        event_types = [event.event_type for event in events]
        self.assertIn("TASK_CANCELLED", event_types)
        self.assertEqual(event_types[-1], "TASK_FAILED")

    def test_cancellation_before_model_result_is_structured(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as store, tempfile.TemporaryDirectory() as sessions:
            result, executor, manager = self.run_case(
                root, store, sessions, lambda ex: InterruptingMain(ex), PassingVerifier()
            )
            self.assert_cancelled(result, executor, manager)
            self.assertFalse(result["changed_files"])
            self.assertFalse(result["undo_available"])

    def test_cancellation_between_tool_actions_preserves_undo(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as store, tempfile.TemporaryDirectory() as sessions:
            result, executor, manager = self.run_case(
                root, store, sessions, lambda ex: InterruptingMain(ex, after_write=True), PassingVerifier()
            )
            self.assert_cancelled(result, executor, manager)
            self.assertEqual(result["changed_files"], ["partial.py"])
            self.assertTrue(result["undo_available"])
            self.assertTrue(Path(root, "partial.py").exists())

    def test_cancellation_during_verification_never_auto_commits(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as store, tempfile.TemporaryDirectory() as sessions:
            result, executor, manager = self.run_case(
                root, store, sessions, WritingMain, InterruptingVerifier()
            )
            self.assert_cancelled(result, executor, manager)
            self.assertEqual(result["changed_files"], ["changed.py"])
            self.assertTrue(result["undo_available"])

    def test_native_backend_cleans_process_tree_on_keyboard_interrupt(self):
        class Process:
            def communicate(self, timeout=None):
                raise KeyboardInterrupt

            def poll(self):
                return None

        process = Process()
        with tempfile.TemporaryDirectory() as root, \
             patch("sable.execution.native.subprocess.Popen", return_value=process), \
             patch.object(NativeExecutionBackend, "_cleanup_process_tree", return_value=(True, "test_cleanup")) as cleanup:
            backend = NativeExecutionBackend(root)
            request = ExecutionRequest(
                argv=["python", "-V"], cwd=Path(root), timeout_seconds=5,
                environment_policy=EnvironmentPolicy.PROJECT,
            )
            with self.assertRaises(KeyboardInterrupt):
                backend.execute(request)
        cleanup.assert_called_once_with(process)

    def test_tool_runtime_emits_process_terminated_on_interrupt(self):
        class InterruptBackend(NativeExecutionBackend):
            name = "interrupt-test"

            def execute(self, request):
                raise KeyboardInterrupt

        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as store:
            executor = ToolExecutor(root, transaction_storage_dir=store)
            executor.execution_backend = InterruptBackend(root)
            events = []
            executor.set_runtime_event_handler(lambda event_type, metadata: events.append((event_type, metadata)))
            with self.assertRaises(KeyboardInterrupt):
                executor._run(["python", "-V"])
        self.assertIn(RuntimeEventType.PROCESS_TERMINATED, [item[0] for item in events])
        terminated = next(item[1] for item in events if item[0] == RuntimeEventType.PROCESS_TERMINATED)
        self.assertTrue(terminated["interrupted"])


if __name__ == "__main__":
    unittest.main()
