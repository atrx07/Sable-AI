import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from sable.security import Workspace, WorkspaceViolation, sanitized_environment
from sable.tools import ToolExecutor


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


class GitConfigBlockedTests(unittest.TestCase):
    def test_git_config_is_blocked(self):
        from sable.config import is_blocked_path

        self.assertTrue(is_blocked_path(".git/config"))
        self.assertTrue(is_blocked_path("repo/.git/config"))
        self.assertTrue(is_blocked_path(".git/credentials"))
        self.assertFalse(is_blocked_path(".gitignore"))
        # The whole .git subtree is blocked, not just config/credentials.
        self.assertTrue(is_blocked_path(".git/hooks/pre-commit"))
        self.assertFalse(is_blocked_path("src/config.py"))

    def test_read_file_refuses_git_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            git_dir = Path(tmp, ".git")
            git_dir.mkdir()
            (git_dir / "config").write_text(
                '[remote "origin"]\n\turl = https://user:s3cret@github.com/x/y.git\n'
            )
            executor = ToolExecutor(tmp)
            result = executor.read_file(".git/config")
            self.assertFalse(result.success)
            self.assertNotIn("s3cret", result.output + result.error)


class PermissionTests(unittest.TestCase):
    """Policy assertions at the dispatch level (the live enforcement point).

    PermissionPolicy.check() was dead code — production dispatch uses
    validate() plus the capability engine — so these tests drive
    ToolExecutor.dispatch instead of querying the policy object.
    """

    def test_plan_is_read_only(self):
        with tempfile.TemporaryDirectory() as root:
            executor = ToolExecutor(root)
            result = executor.dispatch("write_file", {"path": "x", "content": "y"}, mode="plan")
            self.assertFalse(result.success)
            self.assertEqual(result.risk, "blocked")
            self.assertIn("disabled in plan mode", result.error)

    def test_plan_can_list_git_branches_but_not_switch(self):
        with tempfile.TemporaryDirectory() as root:
            executor = ToolExecutor(root)
            self.assertTrue(executor.git_init().success)
            listed = executor.dispatch("git_branch", {"name": ""}, mode="plan")
            self.assertTrue(listed.success, listed.error)
            switched = executor.dispatch("git_branch", {"name": "new-branch"}, mode="plan")
            self.assertFalse(switched.success)
            self.assertEqual(switched.risk, "blocked")
            self.assertIn("disabled in plan mode", switched.error)

    def test_build_blocks_high_risk_without_approval(self):
        # No coarse check() gate anymore: at dispatch these tools hit the
        # mode ceiling in build mode (only yolo can request them, with
        # human approval) and never execute.
        with tempfile.TemporaryDirectory() as root:
            executor = ToolExecutor(root)
            Path(root, "victim.txt").write_text("keep\n")
            for tool, args in (
                ("delete_file", {"path": "victim.txt"}),
                ("git_push", {"branch": "main"}),
                ("run_shell", {"command": "echo hi"}),
            ):
                result = executor.dispatch(tool, args, mode="build")
                self.assertFalse(result.success, tool)
                self.assertIn("not requestable in build mode", result.error, tool)
            self.assertTrue(Path(root, "victim.txt").exists())

    def test_build_blocks_python_dash_c(self):
        with tempfile.TemporaryDirectory() as root:
            executor = ToolExecutor(root)
            result = executor.dispatch(
                "run_command", {"argv": ["python", "-c", "print(1)"]}, mode="build"
            )
            self.assertFalse(result.success)
            self.assertEqual(result.risk, "blocked")
            self.assertEqual(result.security["allowed_by"], "hard_validation")

    def test_yolo_still_relies_on_workspace_hard_boundary(self):
        from sable.capabilities import ApprovalDecision, ApprovalEngine

        with tempfile.TemporaryDirectory() as root:
            outside = Path(root, "outside.txt")
            outside.write_text("outside\n")
            workspace = Path(root, "workspace")
            workspace.mkdir()
            target = workspace / "victim.txt"
            target.write_text("keep\n")
            engine = ApprovalEngine(handler=lambda _request: ApprovalDecision.ALLOW_SESSION)
            executor = ToolExecutor(str(workspace), approval_engine=engine)
            # In yolo with approval, an in-workspace delete runs.
            result = executor.dispatch("delete_file", {"path": "victim.txt"}, mode="yolo")
            self.assertTrue(result.success, result.error)
            self.assertFalse(target.exists())
            # But the workspace boundary still holds: no escape to the parent.
            escaped = executor.dispatch("delete_file", {"path": "../outside.txt"}, mode="yolo")
            self.assertFalse(escaped.success)
            self.assertTrue(outside.exists())


class EnvironmentHardeningTests(unittest.TestCase):
    def test_git_config_environment_triplet_is_removed_atomically(self):
        from sable.security import sanitized_environment

        clean = sanitized_environment(
            {
                "PATH": "tools",
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "http.extraHeader",
                "GIT_CONFIG_VALUE_0": "Authorization: secret",
            }
        )
        self.assertEqual(clean["PATH"], "tools")
        self.assertFalse(any(name.startswith("GIT_CONFIG_") for name in clean))

    def test_common_secret_environment_variables_are_removed(self):
        clean = sanitized_environment(
            {
                "PATH": "/bin",
                "NORMAL_SETTING": "ok",
                "GITHUB_TOKEN": "gh-secret",
                "OPENAI_API_KEY": "sk-secret",
                "AWS_SECRET_ACCESS_KEY": "aws-secret",
                "SSH_AUTH_SOCK": "/tmp/agent.sock",
                "SESSION_NAME": "ordinary-name",
                "KUBECONFIG": "/tmp/kube-config",
                "AZURE_CLIENT_SECRET": "azure-secret",
            }
        )

        self.assertEqual(clean["PATH"], "/bin")
        self.assertEqual(clean["NORMAL_SETTING"], "ok")
        self.assertEqual(clean["SESSION_NAME"], "ordinary-name")
        self.assertNotIn("GITHUB_TOKEN", clean)
        self.assertNotIn("OPENAI_API_KEY", clean)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", clean)
        self.assertNotIn("SSH_AUTH_SOCK", clean)
        self.assertNotIn("KUBECONFIG", clean)
        self.assertNotIn("AZURE_CLIENT_SECRET", clean)
        self.assertEqual(clean["PYTHONNOUSERSITE"], "1")
        self.assertEqual(clean["GIT_TERMINAL_PROMPT"], "0")

    def test_yolo_project_process_still_gets_sanitized_private_environment(self):
        with tempfile.TemporaryDirectory() as root:
            script = Path(root) / "inspect_environment.py"
            script.write_text(
                "import json\n"
                "import os\n"
                "print(json.dumps({\n"
                "    'secret_absent': 'GROQ_API_KEY' not in os.environ,\n"
                "    'private_home': os.environ.get('HOME') == os.environ.get('USERPROFILE'),\n"
                "}))\n",
                encoding="utf-8",
            )
            original = os.environ.get("GROQ_API_KEY")
            os.environ["GROQ_API_KEY"] = "test-secret-present"
            try:
                result = ToolExecutor(root).dispatch(
                    "run_command",
                    {"argv": [sys.executable, script.name]},
                    mode="yolo",
                )
            finally:
                if original is None:
                    os.environ.pop("GROQ_API_KEY", None)
                else:
                    os.environ["GROQ_API_KEY"] = original
            self.assertTrue(result.success, result.error)
            report = json.loads(result.output)
            self.assertTrue(report["secret_absent"])
            self.assertTrue(report["private_home"])
            self.assertTrue(result.execution["environment_sanitized"])


class SecretPatternTests(unittest.TestCase):
    def test_pem_block_is_detected_and_redacted_whole(self):
        from sable.config import contains_secret, redact_secrets

        block = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEpAIBAAKCAQEA7VvKq9X2mN8pL4sT1wQ0x\n"
            "-----END RSA PRIVATE KEY-----\n"
        )
        self.assertTrue(contains_secret(block))
        redacted = redact_secrets(block)
        self.assertNotIn("MIIEpAIBAAKCAQEA7VvKq9X2mN8pL4sT1wQ0x", redacted)
        self.assertNotIn("PRIVATE KEY", redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_truncated_pem_body_is_redacted(self):
        from sable.config import contains_secret, redact_secrets

        # F R2-F5: a truncated PEM (header + base64 body, no END line) left
        # the body behind. The body is the secret, not just the header.
        body = "MIIEpAIBAAKCAQEA7VvKq9X2mN8pL4sT1wQ0x" * 2
        truncated = "-----BEGIN RSA PRIVATE KEY-----\n" + body + "\n" + body + "\n"
        self.assertTrue(contains_secret(truncated))
        redacted = redact_secrets(truncated)
        self.assertNotIn(body, redacted)
        self.assertNotIn("PRIVATE KEY", redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_truncated_pem_header_is_detected(self):
        from sable.config import contains_secret, redact_secrets

        self.assertTrue(contains_secret("-----BEGIN OPENSSH PRIVATE KEY-----\nabc"))
        self.assertNotIn(
            "BEGIN OPENSSH PRIVATE KEY", redact_secrets("-----BEGIN OPENSSH PRIVATE KEY-----\nabc")
        )

    def test_aws_access_key_ids_are_detected(self):
        from sable.config import contains_secret, redact_secrets

        for key in ("AKIAIOSFODNN7EXAMPLE", "ASIAIOSFODNN7EXAMPLE"):
            self.assertTrue(contains_secret(key), key)
            self.assertNotIn(key, redact_secrets(f"key={key}"))
        self.assertFalse(contains_secret("AKIAIOSFODNN7EXAMP"))  # too short

    def test_user_password_urls_are_detected_and_redacted(self):
        from sable.config import contains_secret, redact_secrets

        url = "https://deploy:s3cret-pass@github.com/example/repo.git"
        self.assertTrue(contains_secret(url))
        redacted = redact_secrets(url)
        self.assertNotIn("deploy:s3cret-pass@", redacted)
        self.assertIn("github.com/example/repo.git", redacted)
        # F R2-F5: a password-only URL (empty user) is a secret too.
        empty_user = "https://:s3cret-pass@github.com/example/repo.git"
        self.assertTrue(contains_secret(empty_user))
        redacted_empty = redact_secrets(empty_user)
        self.assertNotIn(":s3cret-pass@", redacted_empty)
        self.assertIn("github.com/example/repo.git", redacted_empty)
        # No false positives on plain URLs.
        self.assertFalse(contains_secret("https://github.com/example/repo.git"))
        self.assertFalse(contains_secret("https://example.com:8080/docs"))
        self.assertFalse(contains_secret("https://token-only@github.com/x/y.git"))


class BlockedGitPathTests(unittest.TestCase):
    def test_whole_git_subtree_is_blocked(self):
        from sable.config import is_blocked_path

        # D R1-F2 / F R2-F3: every .git internal is off-limits to file tools,
        # not just config/credentials — hooks, HEAD, refs, index and logs
        # are all writable plant points for a hook that fires on the user's
        # next manual commit or push.
        for path in (
            ".git/config",
            ".git/credentials",
            ".git/hooks/pre-commit",
            ".git/hooks/post-commit",
            ".git/HEAD",
            ".git/refs/heads/main",
            ".git/index",
            ".git/packed-refs",
            ".git/logs/HEAD",
            ".git/COMMIT_EDITMSG",
            "nested/repo/.git/hooks/pre-push",
        ):
            with self.subTest(path=path):
                self.assertTrue(is_blocked_path(path), path)

    def test_git_block_does_not_catch_lookalikes(self):
        from sable.config import is_blocked_path

        for path in (".gitignore", ".github/workflows/ci.yml", "src/main.py", "my.git/file.txt"):
            with self.subTest(path=path):
                self.assertFalse(is_blocked_path(path), path)

    def test_file_tools_refuse_git_internals(self):
        with tempfile.TemporaryDirectory() as root:
            executor = ToolExecutor(root)
            for tool, args in (
                ("write_file", {"path": ".git/hooks/pre-commit", "content": "echo pwned\n"}),
                ("read_file", {"path": ".git/config"}),
                ("patch_file", {"path": ".git/HEAD", "old": "x", "new": "y"}),
            ):
                with self.subTest(tool=tool):
                    result = executor.dispatch(tool, args, mode="build")
                    self.assertFalse(result.success, tool)
                    self.assertEqual(result.risk, "blocked")
                    self.assertIn("protected path", result.error)


class BypassRegressionTests(unittest.TestCase):
    def _denied_in_build(self, argv):
        with tempfile.TemporaryDirectory() as root:
            executor = ToolExecutor(root)
            result = executor.dispatch("run_command", {"argv": argv}, mode="build")
            self.assertFalse(result.success, argv)
            self.assertEqual(result.risk, "blocked")
            self.assertEqual(result.security["allowed_by"], "hard_validation")

    def test_build_cannot_bypass_git_push_through_run_command(self):
        self._denied_in_build(["git", "push"])

    def test_build_cannot_bypass_delete_with_rm(self):
        self._denied_in_build(["rm", "-rf", "."])

    def test_plan_git_branch_cannot_mutate(self):
        with tempfile.TemporaryDirectory() as root:
            executor = ToolExecutor(root)
            result = executor.dispatch("git_branch", {"name": "new-branch"}, mode="plan")
            self.assertFalse(result.success)
            self.assertEqual(result.risk, "blocked")

    def test_build_blocks_absolute_command_argument(self):
        self._denied_in_build(["python", "/tmp/evil.py"])

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
            with self.subTest(argv=argv):
                self._denied_in_build(argv)

    def test_build_blocks_absolute_path_inside_option(self):
        self._denied_in_build(["make", "--file=/tmp/evil.mk"])


class GitEnvironmentTests(unittest.TestCase):
    def test_git_environment_strips_secrets_keeps_auth(self):
        from sable.security import git_environment

        base = {
            "PATH": "/bin",
            "HOME": "/home/test",
            "GROQ_API_KEY": "gsk_abcdefghijklmnopqrstuvwxyz",
            "OPENAI_API_KEY": "sk-test-secret",
            "GITHUB_TOKEN": "ghp_test",
            "SSH_AUTH_SOCK": "/tmp/agent.sock",
            "SSH_AGENT_PID": "1234",
            "GIT_SSH_COMMAND": "ssh -i /home/test/.ssh/id",
            "GIT_ASKPASS": "/tmp/askpass",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.sshCommand",
            "NORMAL_SETTING": "ok",
        }
        clean = git_environment(base)
        self.assertEqual(clean["PATH"], "/bin")
        self.assertEqual(clean["HOME"], "/home/test")
        self.assertEqual(clean["NORMAL_SETTING"], "ok")
        self.assertEqual(clean["SSH_AUTH_SOCK"], "/tmp/agent.sock")
        self.assertEqual(clean["SSH_AGENT_PID"], "1234")
        self.assertEqual(clean["GIT_SSH_COMMAND"], "ssh -i /home/test/.ssh/id")
        self.assertEqual(clean["GIT_TERMINAL_PROMPT"], "0")
        self.assertNotIn("GROQ_API_KEY", clean)
        self.assertNotIn("OPENAI_API_KEY", clean)
        self.assertNotIn("GITHUB_TOKEN", clean)
        self.assertNotIn("GIT_ASKPASS", clean)
        self.assertNotIn("GIT_CONFIG_COUNT", clean)
        self.assertNotIn("GIT_CONFIG_KEY_0", clean)

    def test_git_environment_drops_secret_values_under_innocent_names(self):
        from sable.config import contains_secret
        from sable.security import git_environment

        # F R2-F6: the name check is value-blind — a token hiding under an
        # innocent variable name must not reach a Git subprocess.
        base = {
            "PATH": "/bin",
            "DATABASE_URL": "postgres://deploy:s3cret-pass@db.internal/app",
            "NORMAL_SETTING": "gsk_abcdefghijklmnopqrstuvwxyz",
            "SSH_AUTH_SOCK": "/tmp/agent.sock",
            "GIT_SSH_COMMAND": "ssh -i /home/test/.ssh/id",
        }
        # The allowlisted auth vars are socket paths / ssh commands, not
        # secrets, and are exempt from the value check either way.
        self.assertFalse(contains_secret(base["SSH_AUTH_SOCK"]))
        self.assertFalse(contains_secret(base["GIT_SSH_COMMAND"]))
        clean = git_environment(base)
        self.assertEqual(clean["PATH"], "/bin")
        self.assertEqual(clean["SSH_AUTH_SOCK"], "/tmp/agent.sock")
        self.assertEqual(clean["GIT_SSH_COMMAND"], "ssh -i /home/test/.ssh/id")
        self.assertNotIn("DATABASE_URL", clean)
        self.assertNotIn("NORMAL_SETTING", clean)
