import io
import tempfile
import unittest
from contextlib import redirect_stdout

from sable.cli_workspace import WorkspaceCommandsMixin
from sable.sessions import SessionManager


class SessionCommandHarness(WorkspaceCommandsMixin):
    def __init__(self, manager):
        self.sessions = manager
        self.session_error = None


class SessionCommandTests(unittest.TestCase):
    def capture(self, method, argument=""):
        stream = io.StringIO()
        with redirect_stdout(stream):
            method(argument)
        return stream.getvalue()

    def test_session_and_trace_commands(self):
        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as storage:
            manager = SessionManager(workspace, storage_dir=storage)
            cli = SessionCommandHarness(manager)
            self.assertIn("SABLE SESSION", self.capture(cli._cmd_session))
            self.assertIn(manager.current.session_id, self.capture(cli._cmd_session, "list"))
            self.assertIn("SESSION_STARTED", self.capture(cli._cmd_trace))
            self.assertIn("Usage", self.capture(cli._cmd_trace, "one two"))

    def test_invalid_session_command_is_safe(self):
        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as storage:
            cli = SessionCommandHarness(SessionManager(workspace, storage_dir=storage))
            self.assertIn("Usage", self.capture(cli._cmd_session, "show"))


if __name__ == "__main__":
    unittest.main()
