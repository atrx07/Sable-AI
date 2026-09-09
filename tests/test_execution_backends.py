import os
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

from sable.execution import (
    BackendAvailability,
    BackendUnavailableError,
    EnforcementLevel,
    EnvironmentPolicy,
    ExecutionBackend,
    ExecutionRequest,
    ExecutionResult,
    NativeExecutionBackend,
    ProotExecutionBackend,
    is_termux_environment,
    select_execution_backend,
)
from sable.tools import ToolExecutor


class UnavailableBackend(ExecutionBackend):
    name = "unavailable"
    guarantees = NativeExecutionBackend().guarantees

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
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(BackendUnavailableError, "unavailable"):
                ToolExecutor(root, execution_backend="proot")

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
            self.assertTrue(result.execution_metadata()["environment_sanitized"])
            self.assertTrue(result.execution_metadata()["private_home"])

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

    def test_project_environment_uses_private_home_and_strips_sensitive_names(self):
        with tempfile.TemporaryDirectory() as root:
            env = dict(os.environ)
            sensitive = [
                "GROQ_API_KEY", "OPENAI_API_KEY", "GH_TOKEN", "AWS_SECRET_ACCESS_KEY",
                "AZURE_CLIENT_SECRET", "KUBECONFIG", "NPM_CONFIG_USERCONFIG", "SSH_AUTH_SOCK",
            ]
            for name in sensitive:
                env[name] = "test-secret-present"
            code = (
                "import json, os; "
                f"names={sensitive!r}; "
                "print(json.dumps({'absent': all(name not in os.environ for name in names), "
                "'home_exists': os.path.isdir(os.environ.get('HOME', '')), "
                "'private_matches': os.environ.get('HOME') == os.environ.get('USERPROFILE')}))"
            )
            result = NativeExecutionBackend(root).execute(ExecutionRequest(
                argv=[sys.executable, "-c", code],
                cwd=Path(root),
                timeout_seconds=5,
                env=env,
            ))
            self.assertTrue(result.success, result.error)
            report = json.loads(result.output)
            self.assertTrue(report["absent"])
            self.assertTrue(report["home_exists"])
            self.assertTrue(report["private_matches"])
            self.assertEqual(result.metadata["environment_policy"], "PROJECT")

    def test_ambient_policy_is_explicit_and_not_reported_as_sanitized(self):
        with tempfile.TemporaryDirectory() as root:
            result = NativeExecutionBackend(root).execute(ExecutionRequest(
                argv=[sys.executable, "-c", "import os; print('yes' if 'GH_TOKEN' in os.environ else 'no')"],
                cwd=Path(root),
                timeout_seconds=5,
                env={"GH_TOKEN": "test-value"},
                environment_policy=EnvironmentPolicy.AMBIENT,
            ))
            self.assertTrue(result.success, result.error)
            self.assertEqual(result.output, "yes")
            self.assertFalse(result.metadata["environment_sanitized"])
            self.assertFalse(result.metadata["private_home"])

    @unittest.skipUnless(os.name == "posix", "POSIX process groups are required")
    def test_timeout_cleans_up_descendant_process_group(self):
        with tempfile.TemporaryDirectory() as root:
            pid_file = Path(root, "child.pid")
            child_code = "import time; time.sleep(30)"
            parent_code = (
                "import pathlib, subprocess, sys, time; "
                f"child=subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
                f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid)); "
                "time.sleep(30)"
            )
            result = NativeExecutionBackend(root).execute(ExecutionRequest(
                argv=[sys.executable, "-c", parent_code],
                cwd=Path(root),
                timeout_seconds=1,
            ))
            self.assertTrue(result.timed_out)
            self.assertTrue(result.terminated)
            self.assertTrue(result.metadata["descendant_cleanup_confirmed"])
            child_pid = int(pid_file.read_text(encoding="utf-8"))

            def running(pid):
                stat = Path(f"/proc/{pid}/stat")
                if stat.exists():
                    try:
                        return stat.read_text().split()[2] != "Z"
                    except (OSError, IndexError):
                        pass
                try:
                    os.kill(pid, 0)
                    return True
                except OSError:
                    return False

            for _ in range(30):
                if not running(child_pid):
                    break
                time.sleep(0.05)
            self.assertFalse(running(child_pid))


class ProotBackendTests(unittest.TestCase):
    def test_termux_detection_is_explicit(self):
        self.assertTrue(is_termux_environment({"TERMUX_VERSION": "1"}))
        self.assertTrue(is_termux_environment({"PREFIX": "/data/data/com.termux/files/usr"}))
        self.assertFalse(is_termux_environment({"PREFIX": "/usr"}))

    def test_proot_requires_termux_executable_and_configured_rootfs(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertFalse(ProotExecutionBackend(root, termux=False, platform="posix").availability().available)
            missing = ProotExecutionBackend(root, termux=True, proot_path="/bin/proot", platform="posix")
            self.assertIn("rootfs", missing.availability().reason)

    def test_proot_command_maps_only_declared_workspace_and_private_home(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as rootfs, tempfile.TemporaryDirectory() as home:
            workspace = Path(root)
            nested = workspace / "src"
            nested.mkdir()
            backend = ProotExecutionBackend(
                workspace,
                rootfs=rootfs,
                proot_path="/data/data/com.termux/files/usr/bin/proot",
                termux=True,
            )
            request = ExecutionRequest(
                argv=["python", "-m", "unittest"],
                cwd=nested,
                timeout_seconds=30,
            )
            command, env, inside_cwd = backend.build_command(request, Path(home))
            self.assertIn(f"{workspace.resolve()}:/workspace", command)
            self.assertIn(f"{Path(home)}:/home/sable", command)
            self.assertEqual(inside_cwd, "/workspace/src")
            self.assertEqual(env["HOME"], "/home/sable")
            self.assertEqual(backend.guarantees.network_isolation, EnforcementLevel.NOT_SUPPORTED)
            self.assertEqual(backend.guarantees.filesystem_namespace, EnforcementLevel.NOT_SUPPORTED)


if __name__ == "__main__":
    unittest.main()
