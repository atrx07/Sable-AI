import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sable.cli_args import ExitCode
from sable.doctor import diagnose, render_text


def config(**updates):
    value = {
        "groq_key_1": "gsk_abcdefghijklmnopqrstuvwxyz",
        "groq_key_2": "",
        "groq_key_3": "",
        "active_key_index": 1,
        "main_model": "main-model",
        "fast_model": "fast-model",
        "mode": "build",
        "execution_backend": "native",
        "proot_rootfs": "",
        "verify_after_changes": True,
        "verification_scope": "affected",
    }
    value.update(updates)
    return value


class DoctorTests(unittest.TestCase):
    def test_healthy_non_git_workspace_is_ready_and_read_only(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as state:
            Path(root, "app.py").write_text("print('ok')\n", encoding="utf-8")
            before = sorted(path.relative_to(root).as_posix() for path in Path(root).rglob("*"))
            report = diagnose(root, config=config(), config_dir=Path(state, "control"))
            after = sorted(path.relative_to(root).as_posix() for path in Path(root).rglob("*"))
        self.assertEqual(report.exit_code, ExitCode.SUCCESS)
        self.assertEqual(before, after)
        text = render_text(report)
        self.assertIn("[INFO] repository: non-Git workspace", text)
        self.assertIn("Mode: offline", text)
        self.assertIn("Result: READY", text)

    def test_default_config_loading_is_read_only(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as state:
            config_file = Path(state, "config.json")
            config_file.write_text(json.dumps(config()), encoding="utf-8")
            with patch.object(Path, "mkdir", side_effect=AssertionError("doctor must not create directories")):
                report = diagnose(
                    root,
                    config_file=config_file,
                    config_dir=Path(state, "control"),
                )
        self.assertEqual(report.exit_code, ExitCode.SUCCESS)

    def test_malformed_config_values_are_reported_without_crashing(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as state:
            report = diagnose(
                root,
                config=config(active_key_index="not-an-index"),
                config_dir=state,
            )
        self.assertEqual(report.exit_code, ExitCode.USAGE)
        self.assertIn("invalid active key selection", render_text(report))

    @unittest.skipUnless(shutil.which("git"), "git executable is required")
    def test_git_workspace_reports_branch_dirty_state_and_origin_boolean(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as state:
            subprocess.run(["git", "init", "-b", "main", root], capture_output=True, check=True)
            Path(root, "dirty.txt").write_text("dirty", encoding="utf-8")
            report = diagnose(root, config=config(), config_dir=state)
        text = render_text(report)
        self.assertIn("repository on main", text)
        self.assertIn("working tree: dirty", text)
        self.assertIn("origin: not configured", text)

    def test_missing_key_is_a_critical_configuration_failure_without_leakage(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as state:
            report = diagnose(root, config=config(groq_key_1=""), config_dir=state)
        self.assertEqual(report.exit_code, ExitCode.USAGE)
        self.assertIn("[FAIL] API key: not configured", render_text(report))

    def test_explicit_unavailable_proot_is_a_backend_failure(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as state:
            report = diagnose(
                root,
                config=config(execution_backend="proot", proot_rootfs=str(Path(root, "missing-rootfs"))),
                config_dir=state,
            )
        self.assertEqual(report.exit_code, ExitCode.BACKEND_UNAVAILABLE)
        backend = next(item for item in report.checks if item.name == "backend")
        self.assertEqual(backend.status, "FAIL")
        self.assertIn("proot", backend.detail.lower())

    def test_unknown_backend_is_a_deterministic_failure(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as state:
            report = diagnose(root, config=config(execution_backend="mystery"), config_dir=state)
        self.assertEqual(report.exit_code, ExitCode.BACKEND_UNAVAILABLE)
        self.assertTrue(any("unknown configured backend" in item.detail for item in report.checks))

    def test_protected_workspace_is_refused_before_discovery(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as state:
            protected = Path(root, ".sable")
            protected.mkdir()
            Path(protected, "config.json").write_text('{"secret":"do-not-read"}', encoding="utf-8")
            with patch("sable.doctor.VerificationDiscovery", side_effect=AssertionError("must not scan")):
                report = diagnose(protected, config=config(), config_dir=state)
        self.assertEqual(report.exit_code, ExitCode.USAGE)
        self.assertIn("protected workspace", render_text(report))

    def test_configured_unavailable_checker_is_reported_without_running_it(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as state:
            Path(root, "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n", encoding="utf-8")
            Path(root, "app.py").write_text("value = 1\n", encoding="utf-8")

            def only_git(name):
                return shutil.which("git") if name == "git" else None

            report = diagnose(root, config=config(), config_dir=state, which=only_git)
        unavailable = [item for item in report.checks if item.section == "Verification" and item.status == "UNAVAILABLE"]
        self.assertTrue(unavailable)
        self.assertTrue(any("Ruff" in item.name for item in unavailable))
        self.assertEqual(report.exit_code, ExitCode.SUCCESS)

    def test_no_primary_or_fallback_storage_is_critical(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as state:
            root_path = Path(root).resolve()

            def access(path, mode):
                return Path(path).resolve() == root_path

            report = diagnose(root, config=config(), config_dir=Path(state, "blocked"), access=access)
        self.assertEqual(report.exit_code, ExitCode.USAGE)
        storage = [item for item in report.checks if item.name.endswith("storage")]
        self.assertTrue(any(item.status == "FAIL" for item in storage))

    def test_unwritable_config_storage_is_critical_even_with_runtime_fallback(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as state:
            state_path = Path(state).resolve()

            def access(path, mode):
                return Path(path).resolve() != state_path

            report = diagnose(root, config=config(), config_dir=Path(state, "control"), access=access)
        self.assertEqual(report.exit_code, ExitCode.USAGE)
        config_storage = next(item for item in report.checks if item.name == "config storage")
        self.assertEqual(config_storage.status, "FAIL")
        session_storage = next(item for item in report.checks if item.name == "session storage")
        self.assertEqual(session_storage.status, "PASS")

    def test_doctor_is_offline_and_never_exposes_key_material(self):
        secret = "gsk_abcdefghijklmnopqrstuvwxyz"
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as state, \
             patch("requests.post", side_effect=AssertionError("network must not be used")):
            report = diagnose(root, config=config(groq_key_1=secret), config_dir=state)
        serialized = json.dumps(report.to_dict()) + render_text(report)
        self.assertNotIn(secret, serialized)
        self.assertIn("API key: configured", serialized)
        self.assertTrue(report.offline)


if __name__ == "__main__":
    unittest.main()
