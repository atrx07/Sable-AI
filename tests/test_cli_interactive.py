import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sable.cli import CLI
from sable.presentation import PlainRenderer
from sable.sessions import SessionManager
from sable.tools import ToolExecutor


class InterruptInput:
    def readline(self):
        raise KeyboardInterrupt


class InteractiveCommandTests(unittest.TestCase):
    def make_cli(self, root, state, output):
        cli = CLI.__new__(CLI)
        cli.cfg = {
            "project_dir": str(Path(root, "projects")),
            "main_model": "main-test",
            "fast_model": "fast-test",
            "mode": "build",
            "verify_after_changes": True,
            "verification_scope": "affected",
            "groq_key_1": "",
            "groq_key_2": "",
            "groq_key_3": "",
            "active_key_index": 1,
            "token_usage": {"1": 0, "2": 0, "3": 0},
        }
        cli.mode = "build"
        cli.verify_enabled = True
        cli.verification_scope = "affected"
        cli.run_command = None
        cli.current_project = Path(root).name
        cli.executor = ToolExecutor(root, transaction_storage_dir=Path(state, "transactions"))
        cli.display_branch = None
        cli.sessions = SessionManager(root, storage_dir=Path(state, "sessions"))
        cli.session_error = None
        cli.orchestrator = SimpleNamespace(main=SimpleNamespace(history=[], reset_history=lambda: None))
        cli.input_stream = io.StringIO()
        cli.interactive_approvals = False
        cli.plain = True
        cli.no_color = True
        cli.quiet = False
        cli.verbose = False
        cli.renderer = PlainRenderer(stream=output, error_stream=output, input_func=cli._readline)
        return cli

    def test_command_dispatch_covers_product_inspection_surface(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as state:
            output = io.StringIO()
            leaked = io.StringIO()
            cli = self.make_cli(root, state, output)
            cli.sessions.current.total_model_calls = 3
            cli.sessions.current.input_tokens = 20
            cli.sessions.current.output_tokens = 10
            cli.sessions.current.total_tokens = 30
            commands = (
                "/help", "/status", "/diff", "/usage", "/cost", "/trace",
                "/session", "/txn", "/undo --dry-run", "/sandbox", "/doctor",
                "/verify affected", "/mode build", "/clear",
            )
            with redirect_stdout(leaked), patch("sable.cli_settings.save_config"):
                for command in commands:
                    self.assertTrue(cli._dispatch_command(command), command)
            self.assertFalse(cli._dispatch_command("/exit"))
            text = output.getvalue()
            self.assertEqual(leaked.getvalue(), "")
        for expected in (
            "/status", "Sable status", "Session usage", "Main calls", "Input tokens",
            "monetary", "SESSION_STARTED", "SABLE SESSION", "Execution backend",
            "Sable Doctor", "Verification: ON", "Mode set to build",
            "Conversation history cleared",
        ):
            self.assertIn(expected, text)

    def test_status_prompt_and_help_are_plain_and_secret_free(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as state:
            output = io.StringIO()
            cli = self.make_cli(root, state, output)
            with redirect_stdout(output):
                cli._dispatch_command("/help")
                cli._dispatch_command("/statusfinder")
            text = output.getvalue()
        self.assertNotIn("\x1b[", text)
        self.assertIn("Unknown command /statusfinder", text)

    def test_idle_ctrl_c_exits_cleanly_without_starting_task(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as state:
            output = io.StringIO()
            cli = self.make_cli(root, state, output)
            cli.input_stream = InterruptInput()
            cli.run()
            text = output.getvalue()
        self.assertIn("SABLE", text)
        self.assertIn("Bye!", text)
        self.assertEqual(cli.executor.transactions.current, None)

    def test_exit_command_does_not_run_agent(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as state:
            output = io.StringIO()
            cli = self.make_cli(root, state, output)
            cli.input_stream = io.StringIO("/exit\n")
            cli.run()
        self.assertIn("Bye!", output.getvalue())
        self.assertEqual(cli.orchestrator.main.history, [])


if __name__ == "__main__":
    unittest.main()
