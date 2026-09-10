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

    def test_auto_push_is_capability_gated_even_in_yolo(self):
        with tempfile.TemporaryDirectory() as tmp:
            ex = ToolExecutor(tmp)
            self.assertTrue(ex.git_init().success)
            ex._git(["config", "user.name", "Sable Test"], "git")
            ex._git(["config", "user.email", "sable@example.invalid"], "git")
            path = Path(tmp, "agent.txt")
            path.write_text("base\n")
            self.assertTrue(ex.git_add(".").success)
            self.assertTrue(ex.git_commit("initial").success)
            path.write_text("agent change\n")
            ex.git_ahead_count = lambda _branch="": 1
            orchestrator = Orchestrator(None, None, ex, auto_commit=True, auto_push=True)
            result = {"changed_files": ["agent.txt"], "tool_results": []}

            orchestrator._apply_git_workflow(
                result,
                "change and publish agent file",
                "yolo",
                preexisting_staged=[],
            )

            self.assertIn("Human approval is required", result["git_push"])
            self.assertEqual(len(result["tool_results"]), 1)
            push = result["tool_results"][0]
            self.assertFalse(push.success)
            self.assertTrue(push.approval_required)
            self.assertEqual(push.security["source"], "RUNTIME")
            self.assertIn("GIT_PUBLISH", push.security["required_capabilities"])
