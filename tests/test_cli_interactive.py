import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sable import config
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
        cli.orchestrator = SimpleNamespace(
            main=SimpleNamespace(history=[], reset_history=lambda: None)
        )
        cli.input_stream = io.StringIO()
        cli.interactive_approvals = False
        cli.plain = True
        cli.no_color = True
        cli.quiet = False
        cli.verbose = False
        cli.renderer = PlainRenderer(stream=output, error_stream=output, input_func=cli._readline)
        return cli

    def test_config_edits_both_models_and_rebuilds_router(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as state:
            cli = self.make_cli(root, state, io.StringIO())
            cli.cfg["groq_key_1"] = "synthetic-key"
            config_file = Path(state, "config.json")
            with (
                patch.object(config, "CONFIG_DIR", Path(state)),
                patch.object(config, "CONFIG_FILE", config_file),
            ):
                for choices, expected_main, expected_fast in (
                    ("new-main\n\n", "new-main", "fast-test"),
                    ("\nnew-fast\n", "new-main", "new-fast"),
                    ("\n\n", "new-main", "new-fast"),
                ):
                    with self.subTest(choices=choices):
                        cli.input_stream = io.StringIO(choices)
                        before = cli.orchestrator
                        cli._cmd_config()
                        self.assertEqual(cli.cfg["main_model"], expected_main)
                        self.assertEqual(cli.cfg["fast_model"], expected_fast)
                        saved = json.loads(config_file.read_text(encoding="utf-8"))
                        self.assertEqual(saved["main_model"], expected_main)
                        self.assertEqual(saved["fast_model"], expected_fast)
                        self.assertEqual(cli.orchestrator.main.router.main_model, expected_main)
                        self.assertEqual(cli.orchestrator.main.router.fast_model, expected_fast)
                        if choices == "\n\n":
                            self.assertIs(cli.orchestrator, before)
                        else:
                            self.assertIsNot(cli.orchestrator, before)

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
                "/help",
                "/status",
                "/diff",
                "/usage",
                "/cost",
                "/trace",
                "/session",
                "/txn",
                "/undo --dry-run",
                "/sandbox",
                "/doctor",
                "/verify affected",
                "/mode build",
                "/clear",
            )
            with redirect_stdout(leaked), patch("sable.cli_settings.save_config"):
                for command in commands:
                    self.assertTrue(cli._dispatch_command(command), command)
            self.assertFalse(cli._dispatch_command("/exit"))
            text = output.getvalue()
            self.assertEqual(leaked.getvalue(), "")
        for expected in (
            "/status",
            "Sable status",
            "Session usage",
            "Main calls",
            "Input tokens",
            "monetary",
            "SESSION_STARTED",
            "SABLE SESSION",
            "Execution backend",
            "Sable Doctor",
            "Verification: ON",
            "Mode set to build",
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
