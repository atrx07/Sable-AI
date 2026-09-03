import tempfile
import unittest
from pathlib import Path

from sable.tools import ToolExecutor


class FailingSecondWriteExecutor(ToolExecutor):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.write_calls = 0

    def _atomic_write_text(self, target, content):
        self.write_calls += 1
        if self.write_calls == 2:
            raise OSError("simulated second-file failure")
        return super()._atomic_write_text(target, content)


class ApplyPatchTests(unittest.TestCase):
    def make_executor(self, cls=ToolExecutor):
        root = tempfile.TemporaryDirectory()
        store = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.addCleanup(store.cleanup)
        return Path(root.name), cls(root.name, transaction_storage_dir=store.name)

    def test_single_hunk_update(self):
        root, ex = self.make_executor()
        root.joinpath("a.txt").write_text("one\ntwo\nthree\n")
        patch = """--- a/a.txt
+++ b/a.txt
@@ -1,3 +1,3 @@
 one
-two
+TWO
 three
"""
        result = ex.apply_patch(patch)
        self.assertTrue(result.success, result.error)
        self.assertEqual(root.joinpath("a.txt").read_text(), "one\nTWO\nthree\n")

    def test_multiple_hunks(self):
        root, ex = self.make_executor()
        root.joinpath("a.txt").write_text("a\nb\nc\nd\ne\n")
        patch = """--- a/a.txt
+++ b/a.txt
@@ -1,2 +1,2 @@
 a
-b
+B
@@ -4,2 +4,2 @@
 d
-e
+E
"""
        result = ex.apply_patch(patch)
        self.assertTrue(result.success, result.error)
        self.assertEqual(root.joinpath("a.txt").read_text(), "a\nB\nc\nd\nE\n")

    def test_multi_file_create_update_delete_is_transactional(self):
        root, ex = self.make_executor()
        root.joinpath("update.txt").write_text("old\n")
        root.joinpath("delete.txt").write_text("gone\n")
        ex.begin_transaction("multi patch")
        patch = """--- a/update.txt
+++ b/update.txt
@@ -1 +1 @@
-old
+new
--- /dev/null
+++ b/create.txt
@@ -0,0 +1 @@
+created
--- a/delete.txt
+++ /dev/null
@@ -1 +0,0 @@
-gone
"""
        result = ex.apply_patch(patch)
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.changed_files, ["update.txt", "create.txt", "delete.txt"])
        self.assertEqual(root.joinpath("update.txt").read_text(), "new\n")
        self.assertEqual(root.joinpath("create.txt").read_text(), "created\n")
        self.assertFalse(root.joinpath("delete.txt").exists())
        self.assertEqual(len(ex.transactions.current.snapshots), 3)
        ex.finish_transaction(result.changed_files)
        self.assertTrue(ex.undo_last_transaction().success)
        self.assertEqual(root.joinpath("update.txt").read_text(), "old\n")
        self.assertFalse(root.joinpath("create.txt").exists())
        self.assertEqual(root.joinpath("delete.txt").read_text(), "gone\n")

    def test_context_mismatch_changes_nothing(self):
        root, ex = self.make_executor()
        root.joinpath("a.txt").write_text("actual\n")
        result = ex.apply_patch("""--- a/a.txt
+++ b/a.txt
@@ -1 +1 @@
-expected
+changed
""")
        self.assertFalse(result.success)
        self.assertIn("context mismatch", result.error.lower())
        self.assertEqual(root.joinpath("a.txt").read_text(), "actual\n")

    def test_malformed_patch_is_rejected(self):
        _, ex = self.make_executor()
        result = ex.apply_patch("this is not a patch")
        self.assertFalse(result.success)
        self.assertIn("invalid patch", result.error.lower())

    def test_incorrect_new_file_coordinates_are_rejected(self):
        root, ex = self.make_executor()
        root.joinpath("a.txt").write_text("one\n")
        result = ex.apply_patch("""--- a/a.txt
+++ b/a.txt
@@ -1 +9 @@
-one
+ONE
""")
        self.assertFalse(result.success)
        self.assertIn("new-file start", result.error.lower())
        self.assertEqual(root.joinpath("a.txt").read_text(), "one\n")

    def test_path_escape_is_rejected(self):
        _, ex = self.make_executor()
        result = ex.apply_patch("""--- /dev/null
+++ b/../outside.txt
@@ -0,0 +1 @@
+nope
""")
        self.assertFalse(result.success)
        self.assertIn("escapes workspace", result.error.lower())

    def test_protected_path_is_rejected(self):
        root, ex = self.make_executor()
        root.joinpath(".env").write_text("safe placeholder\n")
        result = ex.apply_patch("""--- a/.env
+++ b/.env
@@ -1 +1 @@
-safe placeholder
+changed
""")
        self.assertFalse(result.success)
        self.assertIn("protected", result.error.lower())
        self.assertEqual(root.joinpath(".env").read_text(), "safe placeholder\n")

    def test_preparation_failure_is_atomic_across_files(self):
        root, ex = self.make_executor()
        root.joinpath("first.txt").write_text("one\n")
        root.joinpath("second.txt").write_text("two\n")
        result = ex.apply_patch("""--- a/first.txt
+++ b/first.txt
@@ -1 +1 @@
-one
+ONE
--- a/second.txt
+++ b/second.txt
@@ -1 +1 @@
-wrong
+TWO
""")
        self.assertFalse(result.success)
        self.assertEqual(root.joinpath("first.txt").read_text(), "one\n")
        self.assertEqual(root.joinpath("second.txt").read_text(), "two\n")

    def test_runtime_failure_rolls_back_already_applied_files(self):
        root, ex = self.make_executor(FailingSecondWriteExecutor)
        root.joinpath("first.txt").write_text("one\n")
        root.joinpath("second.txt").write_text("two\n")
        result = ex.apply_patch("""--- a/first.txt
+++ b/first.txt
@@ -1 +1 @@
-one
+ONE
--- a/second.txt
+++ b/second.txt
@@ -1 +1 @@
-two
+TWO
""")
        self.assertFalse(result.success)
        self.assertIn("were restored", result.error)
        self.assertEqual(root.joinpath("first.txt").read_text(), "one\n")
        self.assertEqual(root.joinpath("second.txt").read_text(), "two\n")

    def test_model_dispatch_exposes_apply_patch_in_build_mode(self):
        root, ex = self.make_executor()
        result = ex.dispatch(
            "apply_patch",
            {"patch": "--- /dev/null\n+++ b/new.txt\n@@ -0,0 +1 @@\n+value\n"},
            mode="build",
        )
        self.assertTrue(result.success, result.error)
        self.assertEqual(root.joinpath("new.txt").read_text(), "value\n")
