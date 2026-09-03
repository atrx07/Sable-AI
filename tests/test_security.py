import os
import tempfile
import unittest
from pathlib import Path

from sable.security import PermissionPolicy, Workspace, WorkspaceViolation, sanitized_environment


class WorkspaceTests(unittest.TestCase):
    def test_parent_escape_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Workspace(tmp)
            with self.assertRaises(WorkspaceViolation):
                workspace.resolve("../outside.txt")

    def test_absolute_escape_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Workspace(tmp)
            with self.assertRaises(WorkspaceViolation):
                workspace.resolve("/etc/passwd")

    @unittest.skipIf(not hasattr(os, "symlink"), "symlinks unavailable")
    def test_symlink_escape_is_blocked(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            link = Path(root) / "escape"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symlinks are unavailable on this host: {exc}")
            workspace = Workspace(root)
            with self.assertRaises(WorkspaceViolation):
                workspace.resolve("escape/secret.txt")


class PermissionTests(unittest.TestCase):
    def test_plan_is_read_only(self):
        allowed, _ = PermissionPolicy("plan").check("write_file", {"path": "x", "content": "y"})
        self.assertFalse(allowed)

    def test_plan_can_list_git_branches_but_not_switch(self):
        allowed, _ = PermissionPolicy("plan").check("git_branch", {"name": ""})
        self.assertTrue(allowed)
        allowed, _ = PermissionPolicy("plan").check("git_branch", {"name": "new-branch"})
        self.assertFalse(allowed)

    def test_build_blocks_high_risk(self):
        for tool in ("delete_file", "git_push", "run_shell"):
            allowed, _ = PermissionPolicy("build").check(tool, {})
            self.assertFalse(allowed, tool)

    def test_build_blocks_python_dash_c(self):
        allowed, _ = PermissionPolicy("build").check("run_command", {"argv": ["python", "-c", "print(1)"]})
        self.assertFalse(allowed)

    def test_yolo_still_relies_on_workspace_hard_boundary(self):
        allowed, _ = PermissionPolicy("yolo").check("delete_file", {"path": "foo"})
        self.assertTrue(allowed)


class EnvironmentHardeningTests(unittest.TestCase):
    def test_common_secret_environment_variables_are_removed(self):
        clean = sanitized_environment({
            "PATH": "/bin",
            "NORMAL_SETTING": "ok",
            "GITHUB_TOKEN": "gh-secret",
            "OPENAI_API_KEY": "sk-secret",
            "AWS_SECRET_ACCESS_KEY": "aws-secret",
            "SSH_AUTH_SOCK": "/tmp/agent.sock",
            "SESSION_NAME": "ordinary-name",
        })

        self.assertEqual(clean["PATH"], "/bin")
        self.assertEqual(clean["NORMAL_SETTING"], "ok")
        self.assertEqual(clean["SESSION_NAME"], "ordinary-name")
        self.assertNotIn("GITHUB_TOKEN", clean)
        self.assertNotIn("OPENAI_API_KEY", clean)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", clean)
        self.assertNotIn("SSH_AUTH_SOCK", clean)
        self.assertEqual(clean["PYTHONNOUSERSITE"], "1")
        self.assertEqual(clean["GIT_TERMINAL_PROMPT"], "0")


class BypassRegressionTests(unittest.TestCase):
    def test_build_cannot_bypass_git_push_through_run_command(self):
        allowed, _ = PermissionPolicy("build").check("run_command", {"argv": ["git", "push"]})
        self.assertFalse(allowed)

    def test_build_cannot_bypass_delete_with_rm(self):
        allowed, _ = PermissionPolicy("build").check("run_command", {"argv": ["rm", "-rf", "."]})
        self.assertFalse(allowed)

    def test_plan_git_branch_cannot_mutate(self):
        allowed, _ = PermissionPolicy("plan").check("git_branch", {"name": "new-branch"})
        self.assertFalse(allowed)

    def test_build_blocks_absolute_command_argument(self):
        allowed, _ = PermissionPolicy("build").check("run_command", {"argv": ["python", "/tmp/evil.py"]})
        self.assertFalse(allowed)

    def test_build_blocks_package_fetch_subcommands(self):
        cases = (
            ["cargo", "install", "tool"],
            ["cargo", "fetch"],
            ["go", "install", "example.invalid/tool@latest"],
            ["go", "mod", "download"],
            ["npm", "exec", "--yes", "tool"],
            ["npm", "publish"],
            ["pnpm", "dlx", "tool"],
        )
        for argv in cases:
            allowed, _ = PermissionPolicy("build").check("run_command", {"argv": argv})
            self.assertFalse(allowed, argv)

    def test_build_blocks_absolute_path_inside_option(self):
        allowed, _ = PermissionPolicy("build").check(
            "run_command", {"argv": ["make", "--file=/tmp/evil.mk"]}
        )
        self.assertFalse(allowed)
