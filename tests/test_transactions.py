import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sable.orchestrator import Orchestrator
from sable.tools import ToolExecutor
from sable.transactions import RollbackStatus, TransactionStatus, WorkspaceTransactionManager


class WorkspaceTransactionTests(unittest.TestCase):
    def make_executor(self, root, storage):
        return ToolExecutor(str(root), transaction_storage_dir=str(storage))

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

    def test_first_snapshot_is_not_overwritten_by_multiple_edits(self):
        with tempfile.TemporaryDirectory() as root_tmp, tempfile.TemporaryDirectory() as store_tmp:
            root = Path(root_tmp)
            target = root / "a.txt"
            target.write_text("baseline")
            ex = self.make_executor(root, store_tmp)
            ex.begin_transaction("multiple edits")
            self.assertTrue(ex.write_file("a.txt", "first").success)
            self.assertTrue(ex.write_file("a.txt", "second").success)
            self.assertEqual(len(ex.transactions.current.snapshots), 1)
            ex.finish_transaction(["a.txt"])
            self.assertTrue(ex.undo_last_transaction().success)
            self.assertEqual(target.read_text(), "baseline")

    def test_nested_file_uses_portable_transaction_path(self):
        with tempfile.TemporaryDirectory() as root_tmp, tempfile.TemporaryDirectory() as store_tmp:
            root = Path(root_tmp)
            target = root / "nested" / "a.txt"
            target.parent.mkdir()
            target.write_text("before")
            ex = self.make_executor(root, store_tmp)
            ex.begin_transaction("nested")
            changed = ex.write_file("nested/a.txt", "after")
            self.assertEqual(changed.changed_files, ["nested/a.txt"])
            meta = ex.finish_transaction(changed.changed_files)
            tx = ex.transactions.get(meta["transaction_id"])
            self.assertEqual(tx.modified_files, ["nested/a.txt"])
            self.assertTrue(ex.undo_transaction(tx.transaction_id).success)
            self.assertEqual(target.read_text(), "before")

    def test_deleted_file_is_recreated(self):
        with tempfile.TemporaryDirectory() as root_tmp, tempfile.TemporaryDirectory() as store_tmp:
            root = Path(root_tmp)
            target = root / "gone.txt"
            target.write_text("recover me")
            ex = self.make_executor(root, store_tmp)
            ex.begin_transaction("delete")
            self.assertTrue(ex.delete_file("gone.txt").success)
            ex.finish_transaction(["gone.txt"])
            undo = ex.undo_last_transaction()
            self.assertTrue(undo.success, undo.error)
            self.assertEqual(target.read_text(), "recover me")

    def test_external_post_task_edit_is_preserved_as_conflict(self):
        with tempfile.TemporaryDirectory() as root_tmp, tempfile.TemporaryDirectory() as store_tmp:
            root = Path(root_tmp)
            target = root / "shared.txt"
            target.write_text("before")
            ex = self.make_executor(root, store_tmp)
            ex.begin_transaction("edit")
            self.assertTrue(ex.write_file("shared.txt", "sable").success)
            ex.finish_transaction(["shared.txt"])
            target.write_text("user changed after task")

            undo = ex.undo_last_transaction()

            self.assertTrue(undo.success, undo.error)
            self.assertIn("conflicting", undo.output)
            self.assertEqual(target.read_text(), "user changed after task")
            latest = ex.transactions.history[-1]
            self.assertEqual(latest.status, TransactionStatus.PARTIAL_ROLLBACK.value)

    def test_undo_dry_run_does_not_modify_files(self):
        with tempfile.TemporaryDirectory() as root_tmp, tempfile.TemporaryDirectory() as store_tmp:
            root = Path(root_tmp)
            target = root / "a.txt"
            target.write_text("before")
            ex = self.make_executor(root, store_tmp)
            ex.begin_transaction("edit")
            ex.write_file("a.txt", "after")
            meta = ex.finish_transaction(["a.txt"])

            dry = ex.undo_transaction(meta["transaction_id"], dry_run=True)

            self.assertTrue(dry.success, dry.error)
            self.assertIn("would restore 1", dry.output)
            self.assertEqual(target.read_text(), "after")

    def test_repeated_undo_walks_newest_eligible_transactions(self):
        with tempfile.TemporaryDirectory() as root_tmp, tempfile.TemporaryDirectory() as store_tmp:
            root = Path(root_tmp)
            ex = self.make_executor(root, store_tmp)
            ex.begin_transaction("first")
            ex.write_file("first.txt", "one")
            ex.finish_transaction(["first.txt"])
            ex.begin_transaction("second")
            ex.write_file("second.txt", "two")
            ex.finish_transaction(["second.txt"])

            self.assertTrue(ex.undo_last_transaction().success)
            self.assertFalse(root.joinpath("second.txt").exists())
            self.assertTrue(root.joinpath("first.txt").exists())
            self.assertTrue(ex.undo_last_transaction().success)
            self.assertFalse(root.joinpath("first.txt").exists())
            self.assertFalse(ex.undo_last_transaction().success)

    def test_invalid_transaction_id_is_safe(self):
        with tempfile.TemporaryDirectory() as root_tmp, tempfile.TemporaryDirectory() as store_tmp:
            ex = self.make_executor(root_tmp, store_tmp)
            result = ex.undo_transaction("does-not-exist")
            self.assertFalse(result.success)
            self.assertIn("no reversible", result.error.lower())

    def test_checkpoint_restores_last_recorded_state(self):
        with tempfile.TemporaryDirectory() as root_tmp, tempfile.TemporaryDirectory() as store_tmp:
            root = Path(root_tmp)
            target = root / "a.txt"
            target.write_text("baseline")
            ex = self.make_executor(root, store_tmp)
            ex.begin_transaction("checkpoint")
            ex.write_file("a.txt", "first")
            checkpoint = ex.create_transaction_checkpoint("after first")
            ex.write_file("a.txt", "second")

            restored = ex.restore_transaction_checkpoint(checkpoint)

            self.assertTrue(restored.success, restored.error)
            self.assertEqual(target.read_text(), "first")
            ex.rollback_active_transaction()

    def test_checkpoint_removes_file_first_created_after_checkpoint(self):
        with tempfile.TemporaryDirectory() as root_tmp, tempfile.TemporaryDirectory() as store_tmp:
            root = Path(root_tmp)
            root.joinpath("a.txt").write_text("baseline")
            ex = self.make_executor(root, store_tmp)
            ex.begin_transaction("checkpoint new path")
            ex.write_file("a.txt", "first")
            checkpoint = ex.create_transaction_checkpoint("before new file")
            ex.write_file("new.txt", "created later")

            restored = ex.restore_transaction_checkpoint(checkpoint)

            self.assertTrue(restored.success, restored.error)
            self.assertFalse(root.joinpath("new.txt").exists())
            self.assertEqual(root.joinpath("a.txt").read_text(), "first")
            ex.rollback_active_transaction()

    def test_history_persists_and_is_bounded(self):
        with tempfile.TemporaryDirectory() as root_tmp, tempfile.TemporaryDirectory() as store_tmp:
            root = Path(root_tmp)
            store = Path(store_tmp)
            target = root / "a.txt"
            target.write_text("0")
            manager = WorkspaceTransactionManager(root, storage_dir=store, max_transactions=2)
            for index in range(3):
                manager.begin(f"task {index}")
                manager.capture(target)
                target.write_text(str(index + 1))
                manager.record_mutation(["a.txt"])
                manager.finish(["a.txt"])
            self.assertEqual(len(manager.history), 2)
            self.assertEqual([tx.task_summary for tx in manager.history], ["task 1", "task 2"])

            reloaded = WorkspaceTransactionManager(root, storage_dir=store, max_transactions=2)

            self.assertEqual(len(reloaded.history), 2)
            self.assertEqual(reloaded.history[-1].task_summary, "task 2")
            self.assertEqual(reloaded.history[-1].rollback_status, RollbackStatus.AVAILABLE.value)

    def test_non_git_workspace_records_no_git_dependency(self):
        with tempfile.TemporaryDirectory() as root_tmp, tempfile.TemporaryDirectory() as store_tmp:
            root = Path(root_tmp)
            ex = self.make_executor(root, store_tmp)
            ex.begin_transaction("non git")
            ex.write_file("new.txt", "value")
            meta = ex.finish_transaction(["new.txt"])
            tx = ex.transactions.get(meta["transaction_id"])
            self.assertIsNone(tx.repo_root)
            self.assertTrue(ex.undo_transaction(tx.transaction_id).success)
            self.assertFalse(root.joinpath("new.txt").exists())

    def test_git_dirty_baseline_is_marked_and_restored_safely(self):
        with tempfile.TemporaryDirectory() as root_tmp, tempfile.TemporaryDirectory() as store_tmp:
            root = Path(root_tmp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "tests@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Tests"], cwd=root, check=True)
            target = root / "shared.txt"
            target.write_text("committed\n")
            subprocess.run(["git", "add", "shared.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
            target.write_text("user work\n")
            ex = self.make_executor(root, store_tmp)

            ex.begin_transaction("edit dirty file")
            self.assertIn("shared.txt", ex.transactions.current.baseline_unstaged)
            ex.write_file("shared.txt", "sable result\n")
            meta = ex.finish_transaction(["shared.txt"])
            tx = ex.transactions.get(meta["transaction_id"])

            self.assertIn("shared.txt", tx.conflict_sensitive_files)
            self.assertTrue(ex.undo_transaction(tx.transaction_id).success)
            self.assertEqual(target.read_text(), "user work\n")

    def test_unrelated_untracked_user_file_survives_undo(self):
        with tempfile.TemporaryDirectory() as root_tmp, tempfile.TemporaryDirectory() as store_tmp:
            root = Path(root_tmp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            root.joinpath("user-untracked.txt").write_text("keep")
            ex = self.make_executor(root, store_tmp)
            ex.begin_transaction("create another file")
            self.assertIn("user-untracked.txt", ex.transactions.current.baseline_untracked)
            ex.write_file("sable.txt", "remove on undo")
            ex.finish_transaction(["sable.txt"])

            self.assertTrue(ex.undo_last_transaction().success)
            self.assertEqual(root.joinpath("user-untracked.txt").read_text(), "keep")
            self.assertFalse(root.joinpath("sable.txt").exists())

    def test_protected_file_cannot_be_snapshotted(self):
        with tempfile.TemporaryDirectory() as root_tmp, tempfile.TemporaryDirectory() as store_tmp:
            root = Path(root_tmp)
            protected = root / ".env"
            protected.write_text("SECRET=value")
            manager = WorkspaceTransactionManager(root, storage_dir=store_tmp)
            manager.begin("protected")
            with self.assertRaisesRegex(Exception, "protected"):
                manager.capture(protected)

    def test_metadata_redacts_secret_shaped_task_text(self):
        with tempfile.TemporaryDirectory() as root_tmp, tempfile.TemporaryDirectory() as store_tmp:
            manager = WorkspaceTransactionManager(root_tmp, storage_dir=store_tmp)
            token = "ghp_" + "A" * 36
            manager.begin(f"use {token}")
            metadata = next(Path(store_tmp).glob("*/metadata.json")).read_text()
            self.assertNotIn(token, metadata)
            self.assertIn("[REDACTED]", metadata)

    def test_open_transaction_recovers_as_aborted_after_restart(self):
        with tempfile.TemporaryDirectory() as root_tmp, tempfile.TemporaryDirectory() as store_tmp:
            root = Path(root_tmp)
            target = root / "a.txt"
            target.write_text("before")
            manager = WorkspaceTransactionManager(root, storage_dir=store_tmp)
            manager.begin("interrupted")
            manager.capture(target)
            target.write_text("after")
            manager.record_mutation(["a.txt"])

            reloaded = WorkspaceTransactionManager(root, storage_dir=store_tmp)
            tx = reloaded.history[-1]

            self.assertEqual(tx.status, TransactionStatus.ABORTED.value)
            self.assertEqual(tx.rollback_status, RollbackStatus.AVAILABLE.value)
            self.assertEqual(reloaded.undo(tx.transaction_id)["restored"], ["a.txt"])
            self.assertEqual(target.read_text(), "before")


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


class AlwaysFailVerifier:
    def verify(self, changed_files, run_command=None, mode="build"):
        return {"status": "fail", "summary": "fake failure", "checks": []}


class RepairMain:
    def __init__(self, executor):
        self.executor = executor
        self.calls = 0

    def run(self, _message, mode="build"):
        self.calls += 1
        changed = self.executor.write_file("agent.txt", "bad\n" if self.calls == 1 else "fixed\n")
        return {
            "chat_reply": "changed",
            "changes_summary": [changed.output],
            "tool_results": [changed],
            "changed_files": changed.changed_files,
            "steps": 1,
            "tool_calls": 1,
        }


class FailThenPassVerifier:
    def __init__(self):
        self.calls = 0

    def verify(self, changed_files, run_command=None, mode="build"):
        self.calls += 1
        status = "fail" if self.calls == 1 else "pass"
        return {"status": status, "summary": f"fake {status}", "checks": []}


class CrashingMain:
    def __init__(self, executor):
        self.executor = executor

    def run(self, _message, mode="build"):
        self.executor.write_file("agent.txt", "partial\n")
        raise RuntimeError("simulated crash")


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
            tx = ex.transactions.get(result["transaction_id"])
            self.assertEqual(tx.status, TransactionStatus.COMPLETED.value)
            self.assertEqual(tx.verification["status"], "pass")
            self.assertTrue(ex.undo_last_transaction().success)
            self.assertEqual(target.read_text(), "old value\n")

    def test_failed_verification_remains_recoverable(self):
        with tempfile.TemporaryDirectory() as root_tmp, tempfile.TemporaryDirectory() as store_tmp:
            target = Path(root_tmp, "agent.txt")
            target.write_text("old\n")
            ex = ToolExecutor(root_tmp, transaction_storage_dir=store_tmp)
            orchestrator = Orchestrator(FakeMain(ex), AlwaysFailVerifier(), ex, auto_commit=False, max_fix_loops=0)

            result = orchestrator.handle("make a broken change", verify_enabled=True)

            self.assertEqual(result["final_status"], "verification_failed")
            self.assertTrue(result["undo_available"])
            tx = ex.transactions.get(result["transaction_id"])
            self.assertEqual(tx.status, TransactionStatus.FAILED.value)
            self.assertTrue(ex.undo_transaction(tx.transaction_id).success)
            self.assertEqual(target.read_text(), "old\n")

    def test_repair_loop_creates_bounded_checkpoints(self):
        with tempfile.TemporaryDirectory() as root_tmp, tempfile.TemporaryDirectory() as store_tmp:
            target = Path(root_tmp, "agent.txt")
            target.write_text("old\n")
            ex = ToolExecutor(root_tmp, transaction_storage_dir=store_tmp)
            verifier = FailThenPassVerifier()
            orchestrator = Orchestrator(RepairMain(ex), verifier, ex, auto_commit=False, max_fix_loops=1)

            result = orchestrator.handle("repair this", verify_enabled=True)

            self.assertEqual(result["final_status"], "pass")
            self.assertEqual(target.read_text(), "fixed\n")
            self.assertEqual(len(result["transaction_checkpoints"]), 2)
            tx = ex.transactions.get(result["transaction_id"])
            self.assertEqual(len(tx.checkpoints), 2)

    def test_unexpected_exception_reports_rollback(self):
        with tempfile.TemporaryDirectory() as root_tmp, tempfile.TemporaryDirectory() as store_tmp:
            target = Path(root_tmp, "agent.txt")
            target.write_text("old\n")
            ex = ToolExecutor(root_tmp, transaction_storage_dir=store_tmp)
            orchestrator = Orchestrator(CrashingMain(ex), FakeVerifier(), ex, auto_commit=False)

            result = orchestrator.handle("crash", verify_enabled=True)

            self.assertEqual(result["final_status"], "aborted")
            self.assertIn("simulated crash", result["chat_reply"])
            self.assertTrue(result["rollback"]["success"])
            self.assertEqual(target.read_text(), "old\n")

    def test_rollback_failure_does_not_hide_original_exception(self):
        with tempfile.TemporaryDirectory() as root_tmp, tempfile.TemporaryDirectory() as store_tmp:
            target = Path(root_tmp, "agent.txt")
            target.write_text("old\n")
            ex = ToolExecutor(root_tmp, transaction_storage_dir=store_tmp)
            orchestrator = Orchestrator(CrashingMain(ex), FakeVerifier(), ex, auto_commit=False)

            with patch.object(ex, "rollback_active_transaction", side_effect=OSError("storage unavailable")):
                result = orchestrator.handle("crash", verify_enabled=True)

            self.assertEqual(result["final_status"], "aborted")
            self.assertIn("simulated crash", result["chat_reply"])
            self.assertIn("storage unavailable", result["chat_reply"])
            self.assertFalse(result["rollback"]["success"])
            self.assertTrue(result["undo_available"])

    def test_auto_commit_sha_is_attached_to_transaction(self):
        with tempfile.TemporaryDirectory() as root_tmp, tempfile.TemporaryDirectory() as store_tmp:
            root = Path(root_tmp)
            ex = ToolExecutor(root_tmp, transaction_storage_dir=store_tmp)
            self.assertTrue(ex.git_init().success)
            ex._git(["config", "user.name", "Sable Test"], "git")
            ex._git(["config", "user.email", "sable@example.invalid"], "git")
            root.joinpath("agent.txt").write_text("old value\n")
            ex.git_add("agent.txt")
            self.assertTrue(ex.git_commit("initial").success)
            orchestrator = Orchestrator(FakeMain(ex), FakeVerifier(), ex, auto_commit=True)

            result = orchestrator.handle("commit this change", verify_enabled=True)

            self.assertTrue(result.get("commit_sha"))
            tx = ex.transactions.get(result["transaction_id"])
            self.assertEqual(tx.commit_sha, result["commit_sha"])

    def test_auto_commit_skips_preexisting_dirty_touched_file(self):
        with tempfile.TemporaryDirectory() as root_tmp, tempfile.TemporaryDirectory() as store_tmp:
            root = Path(root_tmp)
            ex = ToolExecutor(root_tmp, transaction_storage_dir=store_tmp)
            self.assertTrue(ex.git_init().success)
            ex._git(["config", "user.name", "Sable Test"], "git")
            ex._git(["config", "user.email", "sable@example.invalid"], "git")
            root.joinpath("agent.txt").write_text("committed\n")
            ex.git_add("agent.txt")
            self.assertTrue(ex.git_commit("initial").success)
            root.joinpath("agent.txt").write_text("user work\n")
            orchestrator = Orchestrator(FakeMain(ex), FakeVerifier(), ex, auto_commit=True)

            result = orchestrator.handle("touch dirty file", verify_enabled=True)

            self.assertIn("already dirty", result.get("git_commit", ""))
            self.assertIn("agent.txt", result.get("transaction_conflicts", []))
            self.assertEqual(ex.git_staged_paths(), [])
