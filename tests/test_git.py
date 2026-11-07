import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from sable.tools import ToolExecutor


class ScopedGitTests(unittest.TestCase):
    def test_dedicated_git_uses_explicit_ambient_environment_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            ex = ToolExecutor(tmp)
            self.assertTrue(ex.git_init().success)
            result = ex.git_status()
            self.assertTrue(result.success, result.error)
            self.assertEqual(result.execution["environment_policy"], "AMBIENT")
            self.assertFalse(result.execution["environment_sanitized"])

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

    def test_git_init_uses_current_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            ex = ToolExecutor(tmp)
            Path(tmp, "agent").mkdir()
            self.assertTrue(ex.change_dir("agent").success)

            result = ex.git_init()

            self.assertTrue(result.success, result.error)
            self.assertTrue(Path(tmp, "agent", ".git").is_dir())
            self.assertFalse(Path(tmp, ".git").exists())

    def test_agent_staging_maps_workspace_paths_into_nested_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            ex = ToolExecutor(tmp)
            Path(tmp, "agent").mkdir()
            self.assertTrue(ex.change_dir("agent").success)
            self.assertTrue(ex.git_init().success)
            ex._git(["config", "user.name", "Sable Test"], "git")
            ex._git(["config", "user.email", "sable@example.invalid"], "git")

            tracked = Path(tmp, "agent", "tracked.txt")
            tracked.write_text("base\n")
            self.assertTrue(ex.git_add(".").success)
            self.assertTrue(ex.git_commit("initial").success)

            tracked.write_text("changed\n")
            # Tool changed_files are workspace-root-relative, even when cwd is nested.
            result = ex.git_add_paths(["agent/tracked.txt"])
            self.assertTrue(result.success, result.error)

            staged = ex._git(["diff", "--cached", "--name-only"], "git")
            self.assertEqual(staged.output.strip(), "tracked.txt")

    def test_parent_git_repo_outside_workspace_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", tmp], check=True)
            workspace = Path(tmp, "workspace")
            workspace.mkdir()
            ex = ToolExecutor(str(workspace))

            result = ex.git_status()

            self.assertFalse(result.success)
            self.assertEqual(result.risk, "blocked")
            self.assertIn("outside the sable workspace", result.error.lower())


class GitArgumentSafetyTests(unittest.TestCase):
    def test_staged_secret_scan_uses_unredacted_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            ex = ToolExecutor(tmp)
            self.assertTrue(ex.git_init().success)
            ex._git(["config", "user.name", "Sable Test"], "git")
            ex._git(["config", "user.email", "sable@example.invalid"], "git")
            token = "ghp_" + "A" * 36
            Path(tmp, "leak.txt").write_text(token)
            self.assertTrue(ex.git_add("leak.txt").success)

            result = ex.git_commit("must fail")

            self.assertFalse(result.success)
            self.assertIn("likely secret", result.error.lower())

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
            result = ex.git_set_remote(
                "https://ghp_abcdefghijklmnopqrstuvwxyz123456@github.com/example/repo.git"
            )
            self.assertFalse(result.success)
            self.assertIn("credentials", result.error.lower())


class NoRemoteErrorTests(unittest.TestCase):
    def test_push_without_remote_is_human_readable_not_a_sentinel(self):
        with tempfile.TemporaryDirectory() as tmp:
            ex = ToolExecutor(tmp)
            self.assertTrue(ex.git_init().success)
            result = ex.git_push()
            self.assertFalse(result.success)
            self.assertNotEqual(result.error, "__NO_REMOTE__")
            self.assertIn("No remote", result.error)
            self.assertIn("origin", result.error)
            self.assertIn("git remote add origin", result.error)


class DetachedHeadTests(unittest.TestCase):
    def _detached_repo(self, tmp):
        ex = ToolExecutor(tmp)
        self.assertTrue(ex.git_init().success)
        ex._git(["config", "user.name", "Sable Test"], "git")
        ex._git(["config", "user.email", "sable@example.invalid"], "git")
        Path(tmp, "a.txt").write_text("base\n")
        self.assertTrue(ex.git_add(".").success)
        self.assertTrue(ex.git_commit("initial").success)
        self.assertTrue(ex._git(["checkout", "--detach", "HEAD"], "git").success)
        self.assertEqual(ex.current_branch(), "")
        return ex

    def test_push_and_pull_from_detached_head_refuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            ex = self._detached_repo(tmp)
            push = ex.git_push()
            self.assertFalse(push.success)
            self.assertIn("Detached HEAD", push.error)
            self.assertIn("check out a branch first", push.error)
            pull = ex.git_pull()
            self.assertFalse(pull.success)
            self.assertIn("Detached HEAD", pull.error)

    def test_explicit_branch_push_from_detached_head_still_targets_it(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as remote_dir:
            ex = self._detached_repo(tmp)
            subprocess.run(
                ["git", "init", "-q", "--bare", "-b", "main"], cwd=remote_dir, check=True
            )
            subprocess.run(["git", "remote", "add", "origin", remote_dir], cwd=tmp, check=True)
            result = ex.git_push("main")
            self.assertTrue(result.success, result.error)


class GitCommitOutcomeTests(unittest.TestCase):
    def test_commit_reports_outcome_and_sha(self):
        with tempfile.TemporaryDirectory() as tmp:
            ex = ToolExecutor(tmp)
            self.assertTrue(ex.git_init().success)
            ex._git(["config", "user.name", "Sable Test"], "git")
            ex._git(["config", "user.email", "sable@example.invalid"], "git")
            Path(tmp, "a.txt").write_text("base\n")
            self.assertTrue(ex.git_add(".").success)
            result = ex.git_commit("initial")
            self.assertTrue(result.success)
            self.assertEqual(result.data.get("outcome"), "committed")
            self.assertTrue(result.data.get("commit_sha"))
            self.assertIn(result.data["commit_sha"], result.output)
            self.assertIn("Committed:", result.output)

    def test_nothing_to_commit_reports_outcome_not_a_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            ex = ToolExecutor(tmp)
            self.assertTrue(ex.git_init().success)
            ex._git(["config", "user.name", "Sable Test"], "git")
            ex._git(["config", "user.email", "sable@example.invalid"], "git")
            Path(tmp, "a.txt").write_text("base\n")
            self.assertTrue(ex.git_add(".").success)
            self.assertTrue(ex.git_commit("initial").success)
            result = ex.git_commit("nothing here")
            self.assertTrue(result.success)
            self.assertEqual(result.data.get("outcome"), "nothing_to_commit")
            self.assertNotIn("commit_sha", result.data)
            self.assertEqual(result.output, "Nothing new to commit.")


class GitTerminalPromptTests(unittest.TestCase):
    def test_remote_ops_disable_terminal_prompts(self):
        import http.server
        import threading

        class Unauthorized(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="test"')
                self.end_headers()

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Unauthorized)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                ex = ToolExecutor(tmp, command_timeout=15)
                self.assertTrue(ex.git_init().success)
                url = f"http://127.0.0.1:{server.server_address[1]}/repo.git"
                result = ex._git(["ls-remote", url], "git_status")
                self.assertFalse(result.success)
                combined = ((result.output or "") + (result.error or "")).lower()
                self.assertIn("terminal prompts disabled", combined)
        finally:
            server.shutdown()
            server.server_close()


class GitStateSummaryTests(unittest.TestCase):
    def _identity(self, ex):
        ex._git(["config", "user.name", "Sable Test"], "git")
        ex._git(["config", "user.email", "sable@example.invalid"], "git")

    def test_state_summary_reports_no_upstream_without_remote(self):
        with tempfile.TemporaryDirectory() as tmp:
            ex = ToolExecutor(tmp)
            self.assertTrue(ex.git_init().success)
            summary = ex.git_state_summary()
            self.assertTrue(summary["present"])
            self.assertIsNone(summary["upstream"])
            self.assertIsNone(summary["ahead"])

    def test_state_summary_reports_upstream_and_ahead(self):
        with tempfile.TemporaryDirectory() as tmp:
            origin = Path(tmp, "origin")
            origin.mkdir()
            subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)

            work = Path(tmp, "work")
            work.mkdir()
            ex = ToolExecutor(str(work))
            self.assertTrue(ex.git_init().success)
            self._identity(ex)
            self.assertTrue(ex.git_set_remote(str(origin)).success)
            (work / "a.txt").write_text("a\n")
            self.assertTrue(ex.git_add(".").success)
            self.assertTrue(ex.git_commit("initial").success)
            push = ex._git(["push", "-u", "origin", "main"], "git")
            self.assertTrue(push.success, push.error)
            summary = ex.git_state_summary()
            self.assertEqual(summary["upstream"], "origin/main")
            self.assertEqual(summary["ahead"], 0)
            (work / "b.txt").write_text("b\n")
            self.assertTrue(ex.git_add(".").success)
            self.assertTrue(ex.git_commit("second").success)
            self.assertEqual(ex.git_state_summary()["ahead"], 1)


class RepoConfigExecScanTests(unittest.TestCase):
    def _repo(self, tmp):
        ex = ToolExecutor(tmp)
        self.assertTrue(ex.git_init().success)
        ex._git(["config", "user.name", "Sable Test"], "git")
        ex._git(["config", "user.email", "sable@example.invalid"], "git")
        Path(tmp, "a.txt").write_text("a\n")
        self.assertTrue(ex.git_add(".").success)
        self.assertTrue(ex.git_commit("initial").success)
        return ex

    def test_credential_helper_shell_command_is_refused(self):
        # F R2-F1 P5: credential.helper "!cmd" runs on remote ops.
        with tempfile.TemporaryDirectory() as tmp:
            ex = self._repo(tmp)
            ex._git(["config", "credential.helper", "!echo pwned"], "git")
            result = ex.git_push()
            self.assertFalse(result.success)
            self.assertEqual(result.risk, "blocked")
            self.assertIn("credential.helper", result.error)

    def test_ssh_command_with_shell_split_is_refused(self):
        # F R2-F1 P5: core.sshCommand "…; exit 1" splits the shell spawn.
        with tempfile.TemporaryDirectory() as tmp:
            ex = self._repo(tmp)
            ex._git(["config", "core.sshCommand", "ssh; echo pwned"], "git")
            result = ex.git_pull()
            self.assertFalse(result.success)
            self.assertEqual(result.risk, "blocked")
            self.assertIn("core.sshcommand", result.error.lower())

    def test_include_path_indirection_is_refused(self):
        # F R2-F1 P5: include.path pulls in another config file, which can
        # itself carry an exec-capable key.
        with tempfile.TemporaryDirectory() as tmp:
            evil = Path(tmp, "evil.conf")
            evil.write_text("[credential]\n\thelper = !echo pwned\n")
            ex = self._repo(tmp)
            ex._git(["config", "include.path", str(evil)], "git")
            result = ex.git_push()
            self.assertFalse(result.success)
            self.assertEqual(result.risk, "blocked")
            self.assertIn("include.path", result.error)

    def test_plain_helpers_and_benign_config_allow_remote_ops(self):
        with tempfile.TemporaryDirectory() as tmp:
            origin = Path(tmp, "origin")
            origin.mkdir()
            subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
            work = Path(tmp, "work")
            work.mkdir()
            ex = self._repo(str(work))
            ex._git(["config", "credential.helper", "store"], "git")
            ex._git(["config", "core.fsmonitor", "true"], "git")
            self.assertTrue(ex.git_set_remote(str(origin)).success)
            result = ex.git_push()
            self.assertTrue(result.success, result.error)


class MidRebaseTests(unittest.TestCase):
    def test_conflicting_pull_reports_mid_rebase(self):
        # F R2-F4: a diverged pull --rebase leaves .git/rebase-merge
        # behind. The tool result must say so with the recovery command.
        with tempfile.TemporaryDirectory() as tmp:
            origin = Path(tmp, "origin")
            origin.mkdir()
            subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
            work = Path(tmp, "work")
            work.mkdir()
            ex = ToolExecutor(str(work))
            self.assertTrue(ex.git_init().success)
            ex._git(["config", "user.name", "Sable Test"], "git")
            ex._git(["config", "user.email", "sable@example.invalid"], "git")
            (work / "a.txt").write_text("base\n")
            self.assertTrue(ex.git_add(".").success)
            self.assertTrue(ex.git_commit("initial").success)
            self.assertTrue(ex.git_set_remote(str(origin)).success)
            self.assertTrue(ex.git_push().success)

            other = Path(tmp, "other")
            subprocess.run(
                ["git", "clone", "-q", "-b", "main", str(origin), str(other)], check=True
            )
            subprocess.run(
                ["git", "-C", str(other), "config", "user.name", "Sable Test"], check=True
            )
            subprocess.run(
                ["git", "-C", str(other), "config", "user.email", "sable@example.invalid"],
                check=True,
            )
            (other / "a.txt").write_text("other change\n")
            subprocess.run(["git", "-C", str(other), "add", "."], check=True)
            subprocess.run(["git", "-C", str(other), "commit", "-q", "-m", "other"], check=True)
            subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "main"], check=True)

            (work / "a.txt").write_text("work change\n")
            self.assertTrue(ex.git_add(".").success)
            self.assertTrue(ex.git_commit("work").success)
            result = ex.git_pull()
            self.assertFalse(result.success)
            self.assertTrue((work / ".git" / "rebase-merge").exists())
            self.assertIn("mid-rebase", result.error)
            self.assertIn("git rebase --abort", result.error)


class GitHookHardeningTests(unittest.TestCase):
    def test_runtime_git_ops_do_not_execute_repo_hooks(self):
        with tempfile.TemporaryDirectory() as tmp:
            ex = ToolExecutor(tmp)
            self.assertTrue(ex.git_init().success)
            ex._git(["config", "user.name", "Sable Test"], "git")
            ex._git(["config", "user.email", "sable@example.invalid"], "git")
            Path(tmp, "a.txt").write_text("base\n")
            self.assertTrue(ex.git_add(".").success)
            hook = Path(tmp, ".git", "hooks", "pre-commit")
            hook.write_text("#!/bin/sh\ntouch hook_ran\n")
            hook.chmod(0o755)
            result = ex.git_commit("initial")
            self.assertTrue(result.success, result.error)
            self.assertFalse(Path(tmp, "hook_ran").exists())
            log = ex._git(["log", "--oneline"], "git_log")
            self.assertIn("initial", log.output)

    def test_git_subprocess_env_strips_secrets_but_keeps_auth(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            dump = Path(tmp, "env_dump.txt")
            script = Path(tmp, "fake_ssh.sh")
            script.write_text(f"#!/bin/sh\nenv > {dump}\nexit 1\n")
            script.chmod(0o755)
            ex = ToolExecutor(tmp, command_timeout=15)
            self.assertTrue(ex.git_init().success)
            patched = {
                "GROQ_API_KEY": "gsk_abcdefghijklmnopqrstuvwxyz",
                "OPENAI_API_KEY": "sk-test-secret",
                "SSH_AUTH_SOCK": "/tmp/fake-agent.sock",
                "GIT_SSH_COMMAND": str(script),
                "GIT_CONFIG_COUNT": "1",
            }
            with mock.patch.dict(os.environ, patched):
                result = ex._git(["ls-remote", "ssh://git@example.invalid/repo.git"], "git_status")
            self.assertFalse(result.success)
            self.assertTrue(dump.exists(), "the fake ssh command never ran")
            dumped = dump.read_text()
            self.assertNotIn("GROQ_API_KEY", dumped)
            self.assertNotIn("gsk_abcdefghijklmnopqrstuvwxyz", dumped)
            self.assertNotIn("OPENAI_API_KEY", dumped)
            self.assertNotIn("GIT_CONFIG_COUNT", dumped)
            self.assertIn("SSH_AUTH_SOCK=/tmp/fake-agent.sock", dumped)
            self.assertIn("GIT_TERMINAL_PROMPT=0", dumped)
