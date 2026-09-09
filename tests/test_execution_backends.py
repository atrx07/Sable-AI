import os
import sys
import tempfile
import unittest
from pathlib import Path

from sable.execution import (
    BackendAvailability,
    BackendUnavailableError,
    EnforcementLevel,
    ExecutionBackend,
    ExecutionRequest,
    ExecutionResult,
    NativeExecutionBackend,
    select_execution_backend,
)
from sable.tools import ToolExecutor


class UnavailableBackend(ExecutionBackend):
    name = "unavailable"
    guarantees = NativeExecutionBackend.guarantees

    def availability(self):
        return BackendAvailability(False, "test backend is absent")

    def execute(self, request):
        return ExecutionResult(self.name, False, error="unavailable", guarantees=self.guarantees)


class ExecutionBackendContractTests(unittest.TestCase):
    def test_native_backend_reports_truthful_guarantees(self):
        status = NativeExecutionBackend().status()
        self.assertTrue(status["available"])
        guarantees = status["guarantees"]
        self.assertEqual(guarantees["shell_disabled_by_default"], EnforcementLevel.ENFORCED.value)
        self.assertEqual(guarantees["filesystem_namespace"], EnforcementLevel.NOT_SUPPORTED.value)
        self.assertEqual(guarantees["network_isolation"], EnforcementLevel.NOT_SUPPORTED.value)
        self.assertEqual(guarantees["process_isolation"], EnforcementLevel.NOT_SUPPORTED.value)

    def test_auto_selects_first_available_backend(self):
        selected = select_execution_backend(
            "auto",
            candidates=[UnavailableBackend(), NativeExecutionBackend()],
        )
        self.assertEqual(selected.name, "native")

    def test_explicit_unavailable_backend_fails_closed(self):
        with self.assertRaisesRegex(BackendUnavailableError, "test backend is absent"):
            select_execution_backend("unavailable", candidates=[UnavailableBackend()])
        with self.assertRaisesRegex(BackendUnavailableError, "not registered"):
            select_execution_backend("proot", candidates=[NativeExecutionBackend()])

    def test_native_execution_normalizes_cwd_environment_and_metadata(self):
        with tempfile.TemporaryDirectory() as root:
            env = dict(os.environ)
            env["SABLE_EXECUTION_TEST"] = "present"
            request = ExecutionRequest(
                argv=[
                    sys.executable,
                    "-c",
                    "import os; print(os.getcwd()); print(os.environ.get('SABLE_EXECUTION_TEST'))",
                ],
                cwd=Path(root),
                timeout_seconds=5,
                env=env,
            )
            result = NativeExecutionBackend().execute(request)
            self.assertTrue(result.success, result.error)
            lines = result.output.splitlines()
            self.assertEqual(Path(lines[0]).resolve(), Path(root).resolve())
            self.assertEqual(lines[1], "present")
            self.assertEqual(result.execution_metadata()["backend"], "native")
            self.assertTrue(result.execution_metadata()["environment_supplied"])

    def test_output_and_timeout_are_bounded_and_structured(self):
        with tempfile.TemporaryDirectory() as root:
            backend = NativeExecutionBackend()
            output = backend.execute(ExecutionRequest(
                argv=[sys.executable, "-c", "print('x' * 5000)"],
                cwd=Path(root),
                timeout_seconds=5,
                max_output_chars=512,
            ))
            self.assertTrue(output.success)
            self.assertTrue(output.truncated)
            self.assertLessEqual(len(output.output), 512)

            timeout = backend.execute(ExecutionRequest(
                argv=[sys.executable, "-c", "import time; time.sleep(3)"],
                cwd=Path(root),
                timeout_seconds=1,
            ))
            self.assertFalse(timeout.success)
            self.assertTrue(timeout.timed_out)
            self.assertIn("timed out", timeout.error.lower())

    def test_tool_core_delegates_to_selected_backend(self):
        with tempfile.TemporaryDirectory() as root:
            executor = ToolExecutor(root, execution_backend=NativeExecutionBackend())
            result = executor.run_command([sys.executable, "--version"])
            self.assertTrue(result.success, result.error)
            self.assertEqual(result.execution["backend"], "native")
            self.assertEqual(executor.execution_backend_status()["name"], "native")


if __name__ == "__main__":
    unittest.main()
