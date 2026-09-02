import tempfile
import unittest
from pathlib import Path

from sable.orchestrator import Orchestrator
from sable.tools import ToolExecutor


class WorkspaceTransactionTests(unittest.TestCase):
    def test_existing_and_new_files_are_restored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.joinpath("existing.txt").write_text("before\n")
            ex = ToolExecutor(tmp)

            txid = ex.begin_transaction("edit files")
            self.assertTrue(txid)
            self.assertTrue(ex.patch_file("existing.txt", "before", "after").success)
            self.assertTrue(ex.write_file("new.txt", "created\n").success)

            meta = ex.finish_transaction(["existing.txt", "new.txt"])
            self.assertTrue(meta["undo_available"])
            self.assertEqual(root.joinpath("existing.txt").read_text(), "after\n")
            self.assertTrue(root.joinpath("new.txt").exists())

            undo = ex.undo_last_transaction()
            self.assertTrue(undo.success, undo.error)
            self.assertEqual(root.joinpath("existing.txt").read_text(), "before\n")
            self.assertFalse(root.joinpath("new.txt").exists())

    def test_move_directory_is_reversible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            source.joinpath("code.py").write_text("print('before')\n")
            destination = root / "destination"
            destination.mkdir()
            destination.joinpath("keep.txt").write_text("keep\n")

            ex = ToolExecutor(tmp)
            ex.begin_transaction("move tree")
            moved = ex.move_file("source", "destination")
            self.assertTrue(moved.success, moved.error)
            ex.finish_transaction(moved.changed_files)

            self.assertFalse(source.exists())
            self.assertTrue(destination.joinpath("source", "code.py").exists())

            undo = ex.undo_last_transaction()
            self.assertTrue(undo.success, undo.error)
            self.assertTrue(source.joinpath("code.py").exists())
            self.assertTrue(destination.joinpath("keep.txt").exists())
            self.assertFalse(destination.joinpath("source").exists())

    def test_transaction_budget_can_block_unsafe_large_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp, "large.txt")
            target.write_text("0123456789")
            ex = ToolExecutor(tmp)
            ex.transactions.max_backup_bytes = 4
            ex.begin_transaction("too large")

            result = ex.write_file("large.txt", "replacement")

            self.assertFalse(result.success)
            self.assertIn("backup budget", result.error.lower())
            self.assertEqual(target.read_text(), "0123456789")
            ex.rollback_active_transaction()

    def test_rollback_active_restores_without_creating_undo_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp, "a.txt")
            target.write_text("original")
            ex = ToolExecutor(tmp)
            ex.begin_transaction("failing task")
            self.assertTrue(ex.write_file("a.txt", "changed").success)

            rollback = ex.rollback_active_transaction()

            self.assertTrue(rollback.success, rollback.error)
            self.assertEqual(target.read_text(), "original")
            self.assertFalse(ex.undo_last_transaction().success)


class FakeMain:
    def __init__(self, executor):
        self.executor = executor

    def run(self, _message, mode="build"):
        changed = self.executor.write_file("agent.txt", "new value\n")
        return {
            "chat_reply": "changed",
            "changes_summary": [changed.output],
            "tool_results": [changed],
            "changed_files": changed.changed_files,
            "steps": 1,
            "tool_calls": 1,
        }


class FakeVerifier:
    def verify(self, changed_files, run_command=None, mode="build"):
        return {"status": "pass", "summary": "fake pass", "checks": []}


class OrchestratorTransactionTests(unittest.TestCase):
    def test_completed_agent_task_exposes_undo(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp, "agent.txt")
            target.write_text("old value\n")
            ex = ToolExecutor(tmp)
            orchestrator = Orchestrator(FakeMain(ex), FakeVerifier(), ex, auto_commit=False)

            result = orchestrator.handle("change the file", verify_enabled=True)

            self.assertTrue(result["undo_available"])
            self.assertEqual(target.read_text(), "new value\n")
            self.assertTrue(ex.undo_last_transaction().success)
            self.assertEqual(target.read_text(), "old value\n")
