import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from sable.cli_workspace import WorkspaceCommandsMixin
from sable.tools import ToolExecutor


class CLIHarness(WorkspaceCommandsMixin):
    def __init__(self, executor):
        self.executor = executor


class TransactionCommandTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.store = tempfile.TemporaryDirectory()
        self.executor = ToolExecutor(self.root.name, transaction_storage_dir=self.store.name)
        self.cli = CLIHarness(self.executor)
        target = Path(self.root.name, "a.txt")
        target.write_text("before")
        self.executor.begin_transaction("cli transaction")
        self.executor.write_file("a.txt", "after")
        self.meta = self.executor.finish_transaction(["a.txt"])

    def tearDown(self):
        self.store.cleanup()
        self.root.cleanup()

    def capture(self, method, argument):
        stream = io.StringIO()
        with redirect_stdout(stream):
            method(argument)
        return stream.getvalue()

    def test_undo_dry_run_and_selected_id(self):
        output = self.capture(self.cli._cmd_undo, f"{self.meta['transaction_id']} --dry-run")
        self.assertIn("would restore 1", output)
        self.assertEqual(Path(self.root.name, "a.txt").read_text(), "after")

        output = self.capture(self.cli._cmd_undo, self.meta["transaction_id"])
        self.assertIn("Undid Sable transaction", output)
        self.assertEqual(Path(self.root.name, "a.txt").read_text(), "before")

    def test_transaction_list_and_show(self):
        listing = self.capture(self.cli._cmd_transaction, "list")
        self.assertIn("cli transaction", listing)
        detail = self.capture(self.cli._cmd_transaction, f"show {self.meta['transaction_id']}")
        self.assertIn('"rollback_status"', detail)
        self.assertIn('"changed_files"', detail)

    def test_invalid_transaction_commands_are_safe(self):
        self.assertIn("Usage", self.capture(self.cli._cmd_undo, "one two"))
        self.assertIn("Usage", self.capture(self.cli._cmd_transaction, "show"))
        self.assertIn("Unknown transaction", self.capture(self.cli._cmd_transaction, "show missing"))
