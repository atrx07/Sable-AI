import json
import tempfile
import unittest
from pathlib import Path

from sable.verification import IntegrityStatus, VerificationIntegrityBaseline


def write(root, relative, content):
    path = Path(root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class VerificationIntegrityTests(unittest.TestCase):
    def test_deleted_existing_test_is_blocked_unless_explicitly_requested(self):
        with tempfile.TemporaryDirectory() as root:
            test = write(root, "tests/test_app.py", "def test_app():\n    assert True\n")
            baseline = VerificationIntegrityBaseline.capture(root)
            test.unlink()
            report = baseline.compare(["tests/test_app.py"], user_request="fix the app", after_repair=True)
            self.assertEqual(report.status, IntegrityStatus.BLOCKED)
            self.assertEqual(report.issues[0].code, "TEST_DELETED")
            allowed = baseline.compare(
                ["tests/test_app.py"], user_request="remove the obsolete test", after_repair=True
            )
            self.assertFalse(allowed.blocked)

    def test_blanket_skip_introduction_is_blocked(self):
        with tempfile.TemporaryDirectory() as root:
            path = write(root, "tests/test_app.py", "def test_app():\n    assert True\n")
            baseline = VerificationIntegrityBaseline.capture(root)
            path.write_text("import pytest\npytestmark = pytest.mark.skip\ndef test_app():\n    assert True\n")
            report = baseline.compare(["tests/test_app.py"], user_request="fix app", after_repair=True)
            self.assertTrue(report.blocked)
            self.assertIn("BLANKET_SKIP_ADDED", {issue.code for issue in report.issues})

    def test_changed_file_metadata_is_not_trusted_to_hide_test_weakening(self):
        with tempfile.TemporaryDirectory() as root:
            path = write(root, "tests/test_app.py", "def test_app():\n    assert 1\n    assert 2\n")
            baseline = VerificationIntegrityBaseline.capture(root)
            path.write_text("import pytest\npytestmark = pytest.mark.skip\ndef test_app():\n    assert 1\n")

            report = baseline.compare(["sable/app.py"], user_request="fix app", after_repair=True)

            self.assertTrue(report.blocked)
            self.assertIn("BLANKET_SKIP_ADDED", {issue.code for issue in report.issues})

    def test_assertion_removal_and_pass_replacement_are_reported(self):
        with tempfile.TemporaryDirectory() as root:
            path = write(root, "tests/test_app.py", "def test_app():\n    assert 1\n    assert 2\n    assert 3\n    assert 4\n")
            baseline = VerificationIntegrityBaseline.capture(root)
            path.write_text("def test_app():\n    pass\n")
            report = baseline.compare(["tests/test_app.py"], user_request="fix app", after_repair=True)
            codes = {issue.code for issue in report.issues}
            self.assertIn("ASSERTIONS_REMOVED", codes)
            self.assertIn("ASSERTION_REPLACED_WITH_PASS", codes)
            self.assertTrue(report.blocked)

    def test_user_requested_test_maintenance_is_not_automatically_blocked(self):
        with tempfile.TemporaryDirectory() as root:
            path = write(root, "tests/test_app.py", "def test_app():\n    assert 1\n    assert 2\n    assert 3\n    assert 4\n")
            baseline = VerificationIntegrityBaseline.capture(root)
            path.write_text("def test_app():\n    pass\n")
            report = baseline.compare(
                ["tests/test_app.py"], user_request="update the test fixture and assertions", after_repair=True
            )
            self.assertFalse(report.blocked)
            self.assertEqual(report.status, IntegrityStatus.WARNING)

    def test_verification_script_replaced_with_noop_is_blocked(self):
        with tempfile.TemporaryDirectory() as root:
            package = write(root, "package.json", json.dumps({"scripts": {"test": "vitest run"}}))
            baseline = VerificationIntegrityBaseline.capture(root)
            package.write_text(json.dumps({"scripts": {"test": "echo done"}}), encoding="utf-8")
            report = baseline.compare(["package.json"], user_request="fix implementation", after_repair=True)
            codes = {issue.code for issue in report.issues}
            self.assertIn("VERIFICATION_CONFIG_CHANGED", codes)
            self.assertIn("VERIFICATION_SCRIPT_DISABLED", codes)
            self.assertTrue(report.blocked)

    def test_config_change_is_high_interest_but_not_automatically_blocked(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "pyproject.toml", "[tool.pytest.ini_options]\n")
            baseline = VerificationIntegrityBaseline.capture(root)
            report = baseline.compare(["pyproject.toml"], user_request="fix tests", after_repair=True)
            self.assertEqual(report.status, IntegrityStatus.WARNING)
            self.assertFalse(report.blocked)

    def test_pre_repair_check_is_clear(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "tests/test_app.py", "def test_app():\n    assert True\n")
            baseline = VerificationIntegrityBaseline.capture(root)
            report = baseline.compare(["tests/test_app.py"], user_request="anything", after_repair=False)
            self.assertEqual(report.status, IntegrityStatus.CLEAR)


if __name__ == "__main__":
    unittest.main()
