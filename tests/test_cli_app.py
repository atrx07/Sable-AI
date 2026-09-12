import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sable.capabilities import ApprovalDecision
from sable.cli import CLI
from sable.cli_app import run_cli
from sable.cli_args import ExitCode, exit_code_for_result, parse_cli_args, resolve_workspace
from sable.sessions import SessionManager
from sable.tools import ToolExecutor


class FakeCLI:
    instances = []

    def __init__(self, *, workspace=None, interactive_approvals=True):
        self.workspace = workspace
        self.interactive_approvals = interactive_approvals
        self.mode = "build"
        self.verify_enabled = True
        self.verification_scope = "affected"
        self.ran_shell = False
        self.ran_task = None
        self.printed_result = None
        type(self).instances.append(self)

    def run(self):
        self.ran_shell = True

    def run_once(self, task):
        self.ran_task = task
        return {"final_status": "pass", "runtime_task": {"termination_reason": "VERIFICATION_PASSED"}}

    def _print_result(self, result):
        self.printed_result = result


class CliParsingTests(unittest.TestCase):
    def setUp(self):
        FakeCLI.instances = []

    def test_legacy_and_bare_workspace_forms(self):
        self.assertEqual(parse_cli_args([]).command, "legacy")
        parsed = parse_cli_args(["."])
        self.assertEqual((parsed.command, parsed.workspace), ("chat", "."))

    def test_explicit_chat_run_and_doctor_forms(self):
        chat = parse_cli_args(["chat", "../project"])
        run = parse_cli_args(["run", "Fix the parser", "."])
        doctor = parse_cli_args(["doctor", "."])
        self.assertEqual((chat.command, chat.workspace), ("chat", "../project"))
        self.assertEqual((run.command, run.task, run.workspace), ("run", "Fix the parser", "."))
        self.assertEqual(doctor.command, "doctor")

    def test_global_options_work_before_or_after_command(self):
        before = parse_cli_args(["--plain", "--mode", "plan", "."])
        after = parse_cli_args(["chat", ".", "--no-color", "--verify", "full"])
        self.assertTrue(before.plain)
        self.assertEqual(before.mode, "plan")
        self.assertTrue(after.no_color)
        self.assertEqual(after.verify, "full")

    def test_help_version_missing_task_and_unknown_option(self):
        with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as help_exit:
            parse_cli_args(["--help"])
        self.assertEqual(help_exit.exception.code, 0)
        self.assertEqual(parse_cli_args(["--version"]).command, "version")
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as missing:
            parse_cli_args(["run"])
        self.assertEqual(missing.exception.code, 2)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as unknown:
            parse_cli_args(["--approve-all"])
        self.assertEqual(unknown.exception.code, 2)

    def test_workspace_resolution_handles_current_relative_absolute_spaces_and_nested(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            nested = base / "space project" / "nested"
            nested.mkdir(parents=True)
            self.assertEqual(resolve_workspace(".", cwd=nested), nested.resolve())
            self.assertEqual(resolve_workspace("space project", cwd=base), nested.parent.resolve())
            self.assertEqual(resolve_workspace(str(nested)), nested.resolve())

    def test_nonexistent_and_file_workspaces_are_refused(self):
        with tempfile.TemporaryDirectory() as root:
            file_path = Path(root, "file.txt")
            file_path.write_text("x", encoding="utf-8")
            with self.assertRaises(ValueError):
                resolve_workspace(str(Path(root, "missing")))
            with self.assertRaises(ValueError):
                resolve_workspace(str(file_path))

    def test_protected_workspace_root_is_refused_before_cli_construction(self):
        with tempfile.TemporaryDirectory() as root:
            protected = Path(root, ".sable")
            protected.mkdir()
            with self.assertRaisesRegex(ValueError, "protected path"):
                resolve_workspace(str(protected))

    def test_run_executes_once_without_entering_shell_and_applies_overrides(self):
        with tempfile.TemporaryDirectory() as root:
            code = run_cli(
                ["run", "Fix parser", root, "--mode", "plan", "--verify", "off"],
                cli_factory=FakeCLI,
                stdout=io.StringIO(), stderr=io.StringIO(), stdin_isatty=False,
            )
        instance = FakeCLI.instances[-1]
        self.assertEqual(code, ExitCode.SUCCESS)
        self.assertEqual(instance.ran_task, "Fix parser")
        self.assertFalse(instance.ran_shell)
        self.assertEqual(instance.mode, "plan")
        self.assertFalse(instance.verify_enabled)
        self.assertFalse(instance.interactive_approvals)

    def test_legacy_entry_still_uses_project_slot_mode(self):
        code = run_cli([], cli_factory=FakeCLI, stdout=io.StringIO(), stderr=io.StringIO())
        self.assertEqual(code, ExitCode.SUCCESS)
        self.assertIsNone(FakeCLI.instances[-1].workspace)
        self.assertTrue(FakeCLI.instances[-1].ran_shell)

    def test_invalid_workspace_returns_usage_without_constructing_cli(self):
        errors = io.StringIO()
        code = run_cli(["chat", "definitely-missing-workspace"], cli_factory=FakeCLI, stderr=errors)
        self.assertEqual(code, ExitCode.USAGE)
        self.assertEqual(FakeCLI.instances, [])
        self.assertIn("does not exist", errors.getvalue())

    def test_version_and_doctor_dispatch_without_starting_interactive_cli(self):
        version_output = io.StringIO()
        self.assertEqual(
            run_cli(["--version"], cli_factory=FakeCLI, stdout=version_output),
            ExitCode.SUCCESS,
        )
        self.assertIn("Sable 2.0.0", version_output.getvalue())
        with tempfile.TemporaryDirectory() as root, \
             patch("sable.cli_app._basic_doctor", return_value=ExitCode.SUCCESS) as doctor:
            code = run_cli(["doctor", root], cli_factory=FakeCLI, stdout=io.StringIO())
        self.assertEqual(code, ExitCode.SUCCESS)
        doctor.assert_called_once()
        self.assertEqual(FakeCLI.instances, [])


class WorkspaceBindingTests(unittest.TestCase):
    def test_direct_workspace_is_not_copied_and_all_state_binds_to_it(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as state:
            workspace = Path(root, "space project", "nested")
            workspace.mkdir(parents=True)
            legacy_base = Path(root, "legacy-slots")
            cfg = {
                "project_dir": str(legacy_base), "mode": "build", "verify_after_changes": True,
                "verification_scope": "affected", "command_timeout": 10,
                "execution_backend": "native", "proot_rootfs": "", "main_model": "main",
                "fast_model": "fast", "max_agent_steps": 2, "max_tool_calls": 2,
                "max_fix_loops": 0, "git_auto_commit": False, "git_auto_push": False,
                "temperature": 0.0, "groq_key_1": "", "groq_key_2": "", "groq_key_3": "",
                "active_key_index": 1, "token_usage": {"1": 0, "2": 0, "3": 0},
            }

            def executor_factory(path, **kwargs):
                return ToolExecutor(path, transaction_storage_dir=Path(state, "transactions"), **kwargs)

            def session_factory(path, **kwargs):
                return SessionManager(path, storage_dir=Path(state, "sessions"), **kwargs)

            with patch("sable.cli.load_config", return_value=cfg), \
                 patch("sable.cli.ToolExecutor", side_effect=executor_factory), \
                 patch("sable.cli.SessionManager", side_effect=session_factory):
                cli = CLI(workspace=workspace, interactive_approvals=False)

            self.assertEqual(Path(cli.executor.project_dir), workspace.resolve())
            self.assertEqual(cli.sessions.workspace, workspace.resolve())
            self.assertEqual(cli.executor.transactions.root, workspace.resolve())
            self.assertFalse(legacy_base.exists())
            self.assertTrue(cli.direct_workspace)
            with patch("builtins.input", side_effect=AssertionError("must not prompt")):
                result = cli.run_once("inspect project")
            self.assertEqual(result["final_status"], "configuration_error")

    def test_non_tty_approval_denies_without_reading_input(self):
        cli = CLI.__new__(CLI)
        cli.interactive_approvals = False
        with patch("builtins.input", side_effect=AssertionError("must not prompt")):
            decision = cli._approval_prompt(SimpleNamespace())
        self.assertEqual(decision, ApprovalDecision.DENY)


class ExitCodeTests(unittest.TestCase):
    def test_exit_code_mapping_is_stable(self):
        cases = (
            ({"final_status": "pass"}, ExitCode.SUCCESS),
            ({"final_status": "built"}, ExitCode.SUCCESS),
            ({"final_status": "configuration_error"}, ExitCode.USAGE),
            ({"final_status": "verification_incomplete"}, ExitCode.VERIFICATION),
            ({"final_status": "blocked", "runtime_task": {"termination_reason": "CAPABILITY_DENIED"}}, ExitCode.CAPABILITY_DENIED),
            ({"final_status": "blocked", "runtime_task": {"termination_reason": "BACKEND_UNAVAILABLE"}}, ExitCode.BACKEND_UNAVAILABLE),
            ({"final_status": "provider_error"}, ExitCode.PROVIDER_FAILURE),
            ({"final_status": "cancelled"}, ExitCode.CANCELLED),
            ({"final_status": "aborted"}, ExitCode.INTERNAL_ERROR),
        )
        for result, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(exit_code_for_result(result), expected)


if __name__ == "__main__":
    unittest.main()
