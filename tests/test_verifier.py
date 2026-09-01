import tempfile
import unittest

from sable.tools import ToolExecutor
from sable.verifier import Verifier


class VerificationPolicyTests(unittest.TestCase):
    def test_custom_run_command_uses_build_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            verifier = Verifier(ToolExecutor(tmp))
            result = verifier.verify(["x.py"], run_command="git status", mode="build")

            self.assertEqual(result["status"], "fail")
            self.assertEqual(len(result["checks"]), 1)
            check = result["checks"][0].result
            self.assertFalse(check.success)
            self.assertTrue(check.approval_required)
            self.assertIn("allow-listed", check.error)

    def test_safe_custom_run_command_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            verifier = Verifier(ToolExecutor(tmp))
            result = verifier.verify(["x.py"], run_command="python --version", mode="build")

            self.assertEqual(result["status"], "pass")
