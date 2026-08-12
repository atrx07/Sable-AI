import tempfile
import unittest
from pathlib import Path

from sable.tools import ToolExecutor


class ScopedGitTests(unittest.TestCase):
    def test_agent_staging_does_not_stage_unrelated_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            ex = ToolExecutor(tmp)
            self.assertTrue(ex.git_init().success)
            ex._git(["config", "user.name", "Sable Test"], "git")
            ex._git(["config", "user.email", "sable@example.invalid"], "git")
            Path(tmp, "agent.txt").write_text("base\n")
            Path(tmp, "user.txt").write_text("base\n")
            self.assertTrue(ex.git_add(".").success)
            self.assertTrue(ex.git_commit("initial").success)

            Path(tmp, "agent.txt").write_text("agent change\n")
            Path(tmp, "user.txt").write_text("user change\n")
            self.assertTrue(ex.git_add_paths(["agent.txt"]).success)

            staged = ex._git(["diff", "--cached", "--name-only"], "git")
            unstaged = ex._git(["diff", "--name-only"], "git")
            self.assertEqual(staged.output.strip(), "agent.txt")
            self.assertEqual(unstaged.output.strip(), "user.txt")

class GitArgumentSafetyTests(unittest.TestCase):
    def test_branch_option_injection_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ex = ToolExecutor(tmp)
            self.assertTrue(ex.git_init().success)
            result = ex.git_branch("--help")
            self.assertFalse(result.success)
            self.assertIn("cannot start", result.error.lower())

    def test_invalid_refspec_like_branch_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ex = ToolExecutor(tmp)
            self.assertTrue(ex.git_init().success)
            result = ex.git_push("main:evil")
            self.assertFalse(result.success)
            self.assertIn("invalid git branch", result.error.lower())

    def test_remote_option_injection_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ex = ToolExecutor(tmp)
            self.assertTrue(ex.git_init().success)
            result = ex.git_set_remote("--upload-pack=evil")
            self.assertFalse(result.success)
            self.assertIn("cannot start", result.error.lower())

    def test_remote_with_embedded_token_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ex = ToolExecutor(tmp)
            self.assertTrue(ex.git_init().success)
            result = ex.git_set_remote("https://ghp_abcdefghijklmnopqrstuvwxyz123456@github.com/example/repo.git")
            self.assertFalse(result.success)
            self.assertIn("credentials", result.error.lower())
