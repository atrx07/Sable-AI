import tempfile
import unittest
from pathlib import Path

from sable.orchestrator import Orchestrator
from sable.tools import ToolExecutor


class AutoCommitSafetyTests(unittest.TestCase):
    def test_preexisting_staged_user_work_disables_auto_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            ex = ToolExecutor(tmp)
            self.assertTrue(ex.git_init().success)
            ex._git(["config", "user.name", "Sable Test"], "git")
            ex._git(["config", "user.email", "sable@example.invalid"], "git")

            user_file = Path(tmp, "user.txt")
            agent_file = Path(tmp, "agent.txt")
            user_file.write_text("base\n")
            agent_file.write_text("base\n")
            self.assertTrue(ex.git_add(".").success)
            self.assertTrue(ex.git_commit("initial").success)

            user_file.write_text("user staged work\n")
            self.assertTrue(ex.git_add("user.txt").success)
            staged_before = ex.git_staged_paths()
            self.assertEqual(staged_before, ["user.txt"])

            agent_file.write_text("agent change\n")
            orchestrator = Orchestrator(None, None, ex, auto_commit=True)
            result = {"changed_files": ["agent.txt"]}

            orchestrator._apply_git_workflow(
                result,
                "change agent file",
                "build",
                preexisting_staged=staged_before,
            )

            self.assertIn("Auto-commit skipped", result.get("git_commit", ""))
            self.assertEqual(ex.git_staged_paths(), ["user.txt"])
            unstaged = ex._git(["diff", "--name-only"], "git_status")
            self.assertIn("agent.txt", unstaged.output.splitlines())

    def test_clean_index_allows_scoped_auto_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            ex = ToolExecutor(tmp)
            self.assertTrue(ex.git_init().success)
            ex._git(["config", "user.name", "Sable Test"], "git")
            ex._git(["config", "user.email", "sable@example.invalid"], "git")

            agent_file = Path(tmp, "agent.txt")
            agent_file.write_text("base\n")
            self.assertTrue(ex.git_add(".").success)
            self.assertTrue(ex.git_commit("initial").success)

            agent_file.write_text("agent change\n")
            orchestrator = Orchestrator(None, None, ex, auto_commit=True)
            result = {"changed_files": ["agent.txt"]}

            orchestrator._apply_git_workflow(
                result,
                "change agent file",
                "build",
                preexisting_staged=[],
            )

            self.assertIn("Committed:", result.get("git_commit", ""))
            self.assertEqual(ex.git_staged_paths(), [])
