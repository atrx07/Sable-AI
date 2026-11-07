"""Regression tests: a repo-configured core.fsmonitor must never execute.

A malicious repository can set ``core.fsmonitor`` to an external script.
Git runs that script on routine index-refreshing reads (status/diff/
ls-files), and ``-c core.hooksPath=/dev/null`` does not stop it — the hook
suppression covers hooks, not the filesystem monitor. Every Sable-owned git
subprocess must therefore carry ``-c core.fsmonitor=false`` (see
``sable.security.safe_git_argv``), and the paths that bypassed the tool
layer (ContextEngine, transactions baseline, doctor) must also use the
sanitised environment, since a monitor script would otherwise inherit the
full ambient env, provider keys included.

The fixture is proved live in test_plain_git_executes_monitor: plain git
with no safety flags runs the script. All other tests assert the same
fixture does NOT run it through Sable. One exception: the boolean form
(``core.fsmonitor=true`` + a planted ``fsmonitor-watchman`` hook) is
asserted at argv level, not by marker alone — plain git 2.43.0 never
invokes that hook in this environment (verified with GIT_TRACE), so a
marker-only assertion would pass with the fix reverted and prove nothing.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from sable.context.engine import ContextEngine
from sable.doctor import diagnose
from sable.security import safe_git_argv
from sable.tools import ToolExecutor
from sable.transactions import WorkspaceTransactionManager


def _run(*args, cwd=None, env=None):
    return subprocess.run(list(args), cwd=cwd, env=env, capture_output=True, text=True, timeout=30)


class FsmonitorFixtureMixin:
    def make_repo(self, root, fsmonitor="script"):
        """Init a repo with one commit and a marker-touching monitor script."""
        root = Path(root)
        self.assertTrue(_run("git", "init", "-q", "-b", "main", str(root)).returncode == 0)
        _run("git", "-C", str(root), "config", "user.name", "Sable Test")
        _run("git", "-C", str(root), "config", "user.email", "sable@example.invalid")
        (root / "file.txt").write_text("one\n")
        _run("git", "-C", str(root), "add", ".")
        _run("git", "-C", str(root), "commit", "-qm", "initial")
        script = root / "monitor.sh"
        script.write_text(f"#!/bin/sh\ntouch {root}/MARKER\n")
        script.chmod(0o755)
        if fsmonitor == "script":
            _run("git", "-C", str(root), "config", "core.fsmonitor", str(script))
        elif fsmonitor == "true":
            _run("git", "-C", str(root), "config", "core.fsmonitor", "true")
            hook = root / ".git" / "hooks" / "fsmonitor-watchman"
            hook.write_text(f"#!/bin/sh\ntouch {root}/MARKER\n")
            hook.chmod(0o755)
        elif fsmonitor == "include":
            included = root / "included.gitconfig"
            included.write_text(f"[core]\n\tfsmonitor = {script}\n")
            _run("git", "-C", str(root), "config", "include.path", str(included))
        return root, script

    def marker(self, root):
        return (Path(root) / "MARKER").exists()

    def clear_marker(self, root):
        marker = Path(root) / "MARKER"
        if marker.exists():
            marker.unlink()


class FsmonitorGitTests(FsmonitorFixtureMixin, unittest.TestCase):
    def test_plain_git_executes_monitor_fixture_is_live(self):
        """Previous behavior: plain `git status` runs the monitor script."""
        with tempfile.TemporaryDirectory() as tmp:
            root, _ = self.make_repo(tmp)
            self.assertFalse(self.marker(root))
            _run("git", "-C", str(root), "status", "--short")
            self.assertTrue(self.marker(root), "fixture monitor did not run under plain git")

    def test_hook_suppression_alone_does_not_stop_monitor(self):
        """-c core.hooksPath=/dev/null does not cover fsmonitor."""
        with tempfile.TemporaryDirectory() as tmp:
            root, _ = self.make_repo(tmp)
            _run("git", "-C", str(root), "-c", "core.hooksPath=/dev/null", "status", "--short")
            self.assertTrue(self.marker(root))

    def test_safe_git_argv_carries_both_overrides(self):
        argv = safe_git_argv(["status", "--short"])
        self.assertEqual(
            argv,
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.fsmonitor=false",
                "status",
                "--short",
            ],
        )
        # The helper must not mutate its input.
        args = ["log", "--oneline"]
        safe_git_argv(args)
        self.assertEqual(args, ["log", "--oneline"])

    def test_git_status_does_not_execute_fsmonitor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _ = self.make_repo(tmp)
            ex = ToolExecutor(str(root))
            result = ex.git_status()
            self.assertTrue(result.success, result.error)
            self.assertFalse(self.marker(root))
            # The fixture's own monitor script is untracked and must appear.
            self.assertIn("monitor.sh", result.output)

    def test_git_diff_does_not_execute_fsmonitor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _ = self.make_repo(tmp)
            (root / "file.txt").write_text("two\n")
            ex = ToolExecutor(str(root))
            result = ex.git_diff()
            self.assertTrue(result.success, result.error)
            self.assertFalse(self.marker(root))
            self.assertIn("file.txt", result.output)

    def test_git_state_summary_does_not_execute_fsmonitor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _ = self.make_repo(tmp)
            (root / "untracked.txt").write_text("new\n")
            ex = ToolExecutor(str(root))
            summary = ex.git_state_summary()
            self.assertTrue(summary["present"])
            # monitor.sh is untracked too (it is the fixture's own script).
            self.assertIn("untracked.txt", summary["untracked"])
            self.assertFalse(self.marker(root))

    def test_git_commit_scan_does_not_execute_fsmonitor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _ = self.make_repo(tmp)
            ex = ToolExecutor(str(root))
            (root / "file.txt").write_text("two\n")
            self.assertTrue(ex.git_add(".").success)
            # git_add refreshes the index, so assert here — before the
            # marker is cleared — that the add itself did not run the
            # monitor. Asserting only after the commit (with the marker
            # cleared in between) would leave git_add's own behaviour
            # unasserted.
            self.assertFalse(self.marker(root))
            self.clear_marker(root)
            result = ex.git_commit("change file")
            self.assertTrue(result.success, result.error)
            self.assertFalse(self.marker(root))

    def test_include_path_fsmonitor_does_not_execute(self):
        """-c outranks include.path indirection."""
        with tempfile.TemporaryDirectory() as tmp:
            root, _ = self.make_repo(tmp, fsmonitor="include")
            ex = ToolExecutor(str(root))
            self.assertTrue(ex.git_status().success)
            self.assertFalse(self.marker(root))

    def test_global_config_fsmonitor_does_not_execute(self):
        """-c outranks a user-global ~/.gitconfig monitor."""
        with tempfile.TemporaryDirectory() as tmp:
            root, script = self.make_repo(tmp, fsmonitor=None)
            home = Path(tmp) / "fakehome"
            home.mkdir()
            (home / ".gitconfig").write_text(f"[core]\n\tfsmonitor = {script}\n")
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = str(home)

            def _restore_home():
                if old_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = old_home

            self.addCleanup(_restore_home)
            ex = ToolExecutor(str(root))
            self.assertTrue(ex.git_status().success)
            self.assertFalse(self.marker(root))

    def test_fsmonitor_boolean_form_disabled_at_argv_level(self):
        """core.fsmonitor=true is disabled by the argv flag, not by luck.

        The boolean form names no script: git consults the
        ``fsmonitor-watchman`` hook, and plain git 2.43.0 never invokes
        that hook in this environment (verified with GIT_TRACE across
        status/add/ls-files/diff --cached/update-index --refresh), so a
        marker-only assertion would pass with the fix reverted and prove
        nothing. The load-bearing assertion is therefore the argv Sable
        actually passes on the git_status path: every git argv must carry
        ``-c core.fsmonitor=false``, which disables both the string and
        the boolean form regardless of git's hook-invocation behaviour.
        The marker assertion stays as belt-and-braces.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root, _ = self.make_repo(tmp, fsmonitor="true")
            # The fixture is configured as intended: boolean config set,
            # hook file present and executable.
            self.assertEqual(
                _run("git", "-C", str(root), "config", "core.fsmonitor").stdout.strip(),
                "true",
            )
            hook = root / ".git" / "hooks" / "fsmonitor-watchman"
            self.assertTrue(hook.exists())
            self.assertTrue(os.access(hook, os.X_OK))
            ex = ToolExecutor(str(root))
            seen_argv: list[list[str]] = []
            original_run = ex._run

            def spy(argv, **kwargs):
                if isinstance(argv, list):
                    seen_argv.append(list(argv))
                return original_run(argv, **kwargs)

            ex._run = spy
            self.assertTrue(ex.git_status().success)
            git_argv = [a for a in seen_argv if a and a[0] == "git"]
            self.assertTrue(git_argv, "no git argv was captured on the git_status path")
            for argv in git_argv:
                pairs = list(zip(argv, argv[1:]))
                self.assertIn(("-c", "core.fsmonitor=false"), pairs)
                self.assertIn(("-c", "core.hooksPath=/dev/null"), pairs)
            self.assertFalse(self.marker(root))


class TextconvDriverTests(FsmonitorFixtureMixin, unittest.TestCase):
    def test_textconv_driver_does_not_execute_on_git_diff(self):
        """A repo textconv driver (via .gitattributes) must not run on diffs."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertTrue(_run("git", "init", "-q", "-b", "main", str(root)).returncode == 0)
            _run("git", "-C", str(root), "config", "user.name", "Sable Test")
            _run("git", "-C", str(root), "config", "user.email", "sable@example.invalid")
            (root / "file.txt").write_text("one\n")
            _run("git", "-C", str(root), "add", ".")
            _run("git", "-C", str(root), "commit", "-qm", "initial")
            script = root / "driver.sh"
            script.write_text(f"#!/bin/sh\ntouch {root}/MARKER\n")
            script.chmod(0o755)
            (root / ".gitattributes").write_text("*.txt diff=evil\n")
            _run("git", "-C", str(root), "config", "diff.evil.textconv", str(script))
            _run("git", "-C", str(root), "add", ".gitattributes")
            _run("git", "-C", str(root), "commit", "-qm", "attrs")
            (root / "file.txt").write_text("two\n")
            ex = ToolExecutor(str(root))
            result = ex.git_diff()
            self.assertTrue(result.success, result.error)
            self.assertFalse(self.marker(root))
            self.assertIn("file.txt", result.output)


class FsmonitorBypassPathTests(FsmonitorFixtureMixin, unittest.TestCase):
    def test_context_engine_does_not_execute_fsmonitor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _ = self.make_repo(tmp)
            context = ContextEngine(root).build()
            self.assertTrue(context.git["present"])
            self.assertFalse(self.marker(root))

    def test_transactions_baseline_does_not_execute_fsmonitor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _ = self.make_repo(tmp)
            with tempfile.TemporaryDirectory() as store:
                manager = WorkspaceTransactionManager(root, storage_dir=store)
                manager.begin("probe")
            self.assertFalse(self.marker(root))

    def test_doctor_does_not_execute_fsmonitor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _ = self.make_repo(tmp)
            with tempfile.TemporaryDirectory() as state:
                report = diagnose(
                    root,
                    config_dir=state,
                    config_file=Path(state) / "config.json",
                )
            self.assertFalse(self.marker(root))
            self.assertTrue(any(c.section == "Git" for c in report.checks))


if __name__ == "__main__":
    unittest.main()
