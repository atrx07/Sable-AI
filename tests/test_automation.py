import io
import json
import tempfile
import unittest

from sable.cli_app import run_cli
from sable.cli_args import ExitCode, parse_cli_args
from sable.presentation import create_renderer


class JsonCLI:
    result = {}
    emit_progress = False

    def __init__(self, *, workspace=None, interactive_approvals=True):
        self.workspace = workspace
        self.interactive_approvals = interactive_approvals
        self.mode = "build"
        self.verify_enabled = True
        self.verification_scope = "affected"
        self.renderer = None

    def configure_presentation(self, **options):
        self.renderer = create_renderer(
            stream=options["stdout"], error_stream=options["stderr"],
            plain=options["plain"], no_color=options["no_color"],
            quiet=options["quiet"], verbose=options["verbose"],
            json_output=options["json_output"],
        )

    def run_once(self, _task):
        if type(self).emit_progress:
            self.renderer.status("working")
        return dict(type(self).result)

    def _print_result(self, result):
        self.renderer.render_result(result, verification_enabled=self.verify_enabled)


def runtime(reason, **updates):
    value = {
        "task_id": "task-1", "session_id": "session-1", "transaction_id": "txn-1",
        "termination_reason": reason, "model_turn_count": 3,
        "routing_purposes": ["MAIN_REASONING", "FAST_CONTEXT_SUMMARY", "MAIN_REASONING"],
        "input_tokens": 12, "output_tokens": 8, "total_tokens": 20, "duration_ms": 1250,
    }
    value.update(updates)
    return value


class JsonAutomationTests(unittest.TestCase):
    def execute(self, result, *, extra=None, progress=False):
        JsonCLI.result = result
        JsonCLI.emit_progress = progress
        stdout = io.StringIO()
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as root:
            code = run_cli(
                ["run", "test task", root, "--json", *(extra or [])],
                cli_factory=JsonCLI, stdout=stdout, stderr=stderr, stdin_isatty=False,
            )
        return code, stdout.getvalue(), stderr.getvalue(), json.loads(stdout.getvalue())

    def execute_text(self, result, *, extra=None):
        JsonCLI.result = result
        JsonCLI.emit_progress = False
        stdout = io.StringIO()
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as root:
            code = run_cli(
                ["run", "test task", root, *(extra or [])],
                cli_factory=JsonCLI, stdout=stdout, stderr=stderr, stdin_isatty=False,
            )
        return code, stdout.getvalue(), stderr.getvalue()

    def test_parser_accepts_json_before_or_after_run(self):
        self.assertTrue(parse_cli_args(["--json", "run", "task", "."]).json_output)
        self.assertTrue(parse_cli_args(["run", "task", ".", "--json"]).json_output)

    def test_success_json_is_the_only_stdout_document(self):
        code, raw, errors, value = self.execute({
            "final_status": "pass", "chat_reply": "done", "task_id": "task-1",
            "changed_files": ["sable/app.py"], "transaction_id": "txn-1", "commit_sha": "abc123",
            "verification_loops": [{
                "overall_status": "PASS_WITH_OPTIONAL_SKIPS",
                "checks": [{"check": {"name": "pytest"}, "status": "PASS", "classification": "NONE"}],
            }],
            "runtime_task": runtime("VERIFICATION_PASSED"),
        })
        self.assertEqual(code, ExitCode.SUCCESS)
        self.assertEqual(errors, "")
        self.assertEqual(raw.count("\n"), 1)
        self.assertEqual(value["schema_version"], 1)
        self.assertEqual(value["status"], "completed")
        self.assertTrue(value["verified"])
        self.assertEqual(value["verification_status"], "PASS_WITH_OPTIONAL_SKIPS")
        self.assertEqual(value["changed_files"], ["sable/app.py"])
        self.assertEqual(value["commit"], "abc123")
        self.assertEqual(value["usage"]["main_model_calls"], 2)
        self.assertEqual(value["usage"]["fast_model_calls"], 1)

    def test_failure_policy_cancellation_and_unverified_states_match_exit_codes(self):
        cases = (
            ({"final_status": "verification_failed", "verification_loops": [{"overall_status": "FAIL"}], "runtime_task": runtime("VERIFICATION_FAILED")}, ExitCode.VERIFICATION, "failed", "FAIL"),
            ({"final_status": "verification_incomplete", "verification_loops": [{"overall_status": "INCOMPLETE"}], "runtime_task": runtime("VERIFICATION_INCOMPLETE")}, ExitCode.VERIFICATION, "blocked", "INCOMPLETE"),
            ({"final_status": "blocked", "runtime_task": runtime("CAPABILITY_DENIED")}, ExitCode.CAPABILITY_DENIED, "blocked", "SKIPPED"),
            ({"final_status": "cancelled", "runtime_task": runtime("USER_ABORT")}, ExitCode.CANCELLED, "cancelled", "SKIPPED"),
        )
        for result, expected_code, expected_status, verification in cases:
            with self.subTest(expected_status=expected_status):
                code, _raw, _errors, value = self.execute(result)
                self.assertEqual(code, expected_code)
                self.assertEqual(value["exit_code"], expected_code)
                self.assertEqual(value["status"], expected_status)
                self.assertEqual(value["verification_status"], verification)
                self.assertEqual(value["exit_reason"], result["runtime_task"]["termination_reason"])

        code, _raw, _errors, value = self.execute(
            {"final_status": "built", "runtime_task": runtime("SUCCESS")}, extra=["--verify", "off"]
        )
        self.assertEqual(code, ExitCode.SUCCESS)
        self.assertFalse(value["verification_enabled"])
        self.assertFalse(value["verified"])
        self.assertEqual(value["verification_status"], "SKIPPED")

    def test_json_contract_redacts_secrets_and_omits_raw_tool_output(self):
        secret = "gsk_abcdefghijklmnopqrstuvwxyz"
        _code, raw, _errors, value = self.execute({
            "final_status": "aborted",
            "chat_reply": f"provider error {secret}",
            "tool_results": [{"output": secret, "error": secret}],
            "runtime_task": runtime("UNEXPECTED_ERROR"),
        })
        self.assertNotIn(secret, raw)
        self.assertIn("[REDACTED]", value["summary"])
        self.assertNotIn("tool_results", value)

    def test_partial_runtime_metadata_still_produces_valid_json(self):
        code, raw, _errors, value = self.execute({
            "final_status": "provider_error",
            "changed_files": None,
            "trace_errors": None,
            "runtime_task": {
                "routing_purposes": None,
                "model_turn_count": "unknown",
                "input_tokens": None,
                "duration_ms": "unknown",
            },
        })
        self.assertEqual(code, ExitCode.PROVIDER_FAILURE)
        self.assertEqual(json.loads(raw), value)
        self.assertEqual(value["changed_files"], [])
        self.assertEqual(value["usage"]["main_model_calls"], 0)
        self.assertEqual(value["duration_ms"], 0)

    def test_json_progress_uses_stderr_without_contaminating_stdout(self):
        _code, raw, errors, value = self.execute({
            "final_status": "pass",
            "verification_loops": [{"overall_status": "PASS"}],
            "runtime_task": runtime("VERIFICATION_PASSED"),
        }, progress=True)
        self.assertEqual(json.loads(raw), value)
        self.assertIn("[status] working", errors)
        self.assertNotIn("working", raw)

    def test_non_tty_plain_and_quiet_outputs_are_stable_and_ansi_free(self):
        result = {"final_status": "built", "chat_reply": "done", "runtime_task": runtime("SUCCESS")}
        code, plain, errors = self.execute_text(result, extra=["--plain", "--verify", "off"])
        self.assertEqual(code, ExitCode.SUCCESS)
        self.assertIn("COMPLETED · UNVERIFIED", plain)
        self.assertNotIn("\x1b[", plain + errors)

        code, quiet, errors = self.execute_text(result, extra=["--quiet", "--verify", "off"])
        self.assertEqual(code, ExitCode.SUCCESS)
        self.assertEqual(quiet, "BUILT\n")
        self.assertEqual(errors, "")
        self.assertNotIn("\x1b[", quiet)

    def test_json_is_rejected_for_interactive_command(self):
        errors = io.StringIO()
        code = run_cli(["chat", ".", "--json"], cli_factory=JsonCLI, stderr=errors)
        self.assertEqual(code, ExitCode.USAGE)
        self.assertIn("only with `sable run`", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
