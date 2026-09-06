import json
import tempfile
import unittest
from pathlib import Path

from sable.runtime import RuntimeEventType, RuntimePhase, RuntimeTask, TerminalStatus, TerminationReason
from sable.sessions import SessionManager


class SessionManagerTests(unittest.TestCase):
    def make_task(self, workspace: str, request: str = "inspect repository") -> RuntimeTask:
        task = RuntimeTask.create(request, workspace)
        task.start()
        task.transition(RuntimePhase.CONTEXT, reason="test_context")
        task.transaction_id = "txn-test"
        task.emit_event(RuntimeEventType.TRANSACTION_STARTED, token="gsk_abcdefghijklmnopqrstuvwxyz")
        task.transition(RuntimePhase.REPORT, reason="test_done")
        task.model_turn_count = 2
        task.tool_call_count = 1
        task.input_tokens = 10
        task.output_tokens = 20
        task.total_tokens = 30
        task.changed_files = ["src/main.py"]
        task.terminate(TerminalStatus.COMPLETED, TerminationReason.SUCCESS)
        return task

    def test_sessions_resume_and_trace_persist_across_manager_instances(self):
        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as storage:
            first = SessionManager(workspace, storage_dir=storage, main_model="main-a", fast_model="fast-a")
            session_id = first.current.session_id
            task = self.make_task(workspace)
            first.record_task(task)

            second = SessionManager(workspace, storage_dir=storage, main_model="main-b", fast_model="fast-b")
            self.assertEqual(second.current.session_id, session_id)
            self.assertEqual(second.current.main_model, "main-b")
            self.assertEqual(second.current.total_tokens, 30)
            self.assertEqual(second.read_task(task.task_id)["task_id"], task.task_id)
            events = second.trace(task_id=task.task_id, limit=100)
            self.assertEqual(events[0].event_type, "TASK_STARTED")
            self.assertEqual(events[-1].event_type, "TASK_COMPLETED")
            self.assertTrue(all(event.session_id == session_id for event in events))
            trace_text = (Path(storage) / session_id / "events.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("gsk_abcdefghijklmnopqrstuvwxyz", trace_text)
            self.assertIn("[REDACTED]", trace_text)

    def test_session_rotation_and_retention_are_bounded(self):
        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as storage:
            manager = SessionManager(workspace, storage_dir=storage, max_sessions=2)
            first_id = manager.current.session_id
            manager.start_new_session()
            second_id = manager.current.session_id
            manager.start_new_session()
            self.assertNotEqual(second_id, manager.current.session_id)
            listed = manager.list_sessions(limit=20)
            self.assertLessEqual(len(listed), 2)
            self.assertFalse((Path(storage) / first_id).exists())
            self.assertEqual(manager.get(second_id).status, "CLOSED")

    def test_corrupt_metadata_and_trace_lines_are_ignored(self):
        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as storage:
            manager = SessionManager(workspace, storage_dir=storage)
            session_dir = Path(storage) / manager.current.session_id
            (session_dir / "bad.json").write_text("not json", encoding="utf-8")
            (Path(storage) / "broken-session").mkdir()
            (Path(storage) / "broken-session" / "metadata.json").write_text("[]", encoding="utf-8")
            with (session_dir / "events.jsonl").open("a", encoding="utf-8") as handle:
                handle.write("not json\n")
            reloaded = SessionManager(workspace, storage_dir=storage)
            self.assertEqual(reloaded.current.session_id, manager.current.session_id)
            events = reloaded.trace()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].event_type, "SESSION_STARTED")

    def test_trace_and_task_outputs_have_bounded_lines(self):
        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as storage:
            manager = SessionManager(workspace, storage_dir=storage, max_events=10, max_trace_bytes=4096)
            for index in range(15):
                task = self.make_task(workspace, request=f"request {index}")
                task.emit_event(RuntimeEventType.TOOL_RESULT, output="x" * 10000)
                manager.record_task(task)
            trace_path = Path(storage) / manager.current.session_id / "events.jsonl"
            lines = trace_path.read_text(encoding="utf-8").splitlines()
            self.assertLessEqual(len(lines), 10)
            self.assertLessEqual(trace_path.stat().st_size, 4096)
            for line in lines:
                self.assertLessEqual(len(line.encode("utf-8")), 5000)

    def test_start_new_session_closes_previous(self):
        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as storage:
            manager = SessionManager(workspace, storage_dir=storage)
            previous = manager.current.session_id
            current = manager.start_new_session()
            self.assertEqual(manager.get(previous).status, "CLOSED")
            self.assertEqual(current.status, "ACTIVE")
            raw = json.loads((Path(storage) / previous / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(raw["status"], "CLOSED")


if __name__ == "__main__":
    unittest.main()
