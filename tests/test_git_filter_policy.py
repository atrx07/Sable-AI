"""Regression tests: repo-configured clean/smudge filters must not execute
except under the git filter policy (issue #23).

PR #22 stopped a repo-configured ``core.fsmonitor`` from executing on
routine inspection, but Git has no ``-c`` off-switch for clean/smudge
filters: git must run a file through its clean filter to hash it, so
``git status`` / ``git diff`` / ``git add`` execute the filter on every
inspection of a modified file, and ``git switch`` / ``git stash`` run the
smudge filter. Sable therefore scans the effective config
(``git config --list``, all scopes) before every Sable-owned Git operation
and applies ``git_filter_policy``:

- ``strict`` (default): only exact git-lfs driver commands are allowed;
  every other filter driver is refused everywhere, including reads.
- ``permissive`` (opt-in): LFS commands are prefix-matched, read-only
  operations warn and continue, and only mutating operations refuse a
  non-LFS driver.

The fixtures are proved live in test_plain_git_executes_clean_filter and
test_plain_git_executes_smudge_filter: plain git runs the marker scripts.
All other tests assert the same fixtures do NOT run them through Sable
except where the active policy explicitly allows it.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from sable.context.engine import ContextEngine
from sable.doctor import diagnose
from sable.security import (
    check_git_filter_policy,
    is_read_only_git_args,
    resolve_filter_policy,
)
from sable.tools import ToolExecutor
from sable.transactions import WorkspaceTransactionManager


def _run(*args, cwd=None, env=None):
    return subprocess.run(list(args), cwd=cwd, env=env, capture_output=True, text=True, timeout=30)


class FilterFixtureMixin:
    def make_repo(self, root, *, driver="clean"):
        """Init a repo with one committed .bin file and a marker filter.

        driver="clean": ``filter.evil.clean`` touches MARKER; the file is
        left modified so every inspection hashes it through the filter.
        driver="smudge": two branches with different .bin contents; the
        smudge script cats stdin (so checkout still works) and touches
        MARKER, so switching branches runs it.
        driver="lfs": a fake ``git-lfs`` shim on PATH; filter.lfs uses the
        canonical ``git lfs install`` commands, and the shim touches
        LFS_MARKER so the test proves the allowed command actually ran.
        driver="process": like "lfs" but only ``filter.lfs.process`` is
        set, to ``git-lfs filter-process``.
        driver=None: plain repo with no filter configured.
        """
        root = Path(root)
        self.assertTrue(_run("git", "init", "-q", "-b", "main", str(root)).returncode == 0)
        _run("git", "-C", str(root), "config", "user.name", "Sable Test")
        _run("git", "-C", str(root), "config", "user.email", "sable@example.invalid")
        (root / "data.bin").write_text("one\n")
        script = root / "driver.sh"
        script.write_text(f"#!/bin/sh\ntouch {root}/MARKER\n")
        script.chmod(0o755)
        if driver == "clean":
            (root / ".gitattributes").write_text("*.bin filter=evil\n")
            _run("git", "-C", str(root), "config", "filter.evil.clean", str(script))
            _run("git", "-C", str(root), "add", ".")
            _run("git", "-C", str(root), "commit", "-qm", "initial")
            (root / "data.bin").write_text("two\n")
        elif driver == "smudge":
            smudge = root / "smudge.sh"
            smudge.write_text(f"#!/bin/sh\ncat\ntouch {root}/MARKER\n")
            smudge.chmod(0o755)
            (root / ".gitattributes").write_text("*.bin filter=evil\n")
            _run("git", "-C", str(root), "config", "filter.evil.smudge", str(smudge))
            _run("git", "-C", str(root), "add", ".")
            _run("git", "-C", str(root), "commit", "-qm", "A")
            _run("git", "-C", str(root), "switch", "-q", "-c", "other")
            (root / "data.bin").write_text("two\n")
            _run("git", "-C", str(root), "commit", "-qam", "B")
            _run("git", "-C", str(root), "switch", "-q", "main")
        elif driver in {"lfs", "process"}:
            bin_dir = Path(root) / "fakebin"
            bin_dir.mkdir()
            shim = bin_dir / "git-lfs"
            shim.write_text(
                "#!/bin/sh\ncat >/dev/null\n"
                f"touch {root}/LFS_MARKER\n"
            )
            shim.chmod(0o755)
            old_path = os.environ.get("PATH", "")
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{old_path}"
            self.addCleanup(lambda: os.environ.__setitem__("PATH", old_path))
            if driver == "lfs":
                (root / ".gitattributes").write_text("*.bin filter=lfs\n")
                _run("git", "-C", str(root), "config", "filter.lfs.clean", "git-lfs clean -- %f")
                _run(
                    "git", "-C", str(root), "config", "filter.lfs.smudge", "git-lfs smudge -- %f"
                )
                _run("git", "-C", str(root), "add", ".")
                _run("git", "-C", str(root), "commit", "-qm", "initial")
                (root / "data.bin").write_text("two\n")
            else:
                # The process driver is configured only AFTER the initial
                # commit: a fake shim cannot speak git's filter-process
                # pkt-line protocol, so plain git would block on `git add`
                # waiting for a protocol response. The policy decision is
                # what the process test proves (see the test), and it needs
                # no Git operation to run the driver.
                _run("git", "-C", str(root), "add", ".")
                _run("git", "-C", str(root), "commit", "-qm", "initial")
                (root / ".gitattributes").write_text("*.bin filter=lfs\n")
                _run(
                    "git", "-C", str(root), "config", "filter.lfs.process", "git-lfs filter-process"
                )
                (root / "data.bin").write_text("two\n")
        else:
            _run("git", "-C", str(root), "add", ".")
            _run("git", "-C", str(root), "commit", "-qm", "initial")
        # Fixture setup itself may run a filter (the smudge switch back to
        # main, the LFS add); start every test with no marker present so
        # assertions prove the Sable call under test ran (or refused) it.
        for name in ("MARKER", "LFS_MARKER"):
            marker = root / name
            if marker.exists():
                marker.unlink()
        return root

    def marker(self, root, name="MARKER"):
        return (Path(root) / name).exists()

    def clear_marker(self, root, name="MARKER"):
        marker = Path(root) / name
        if marker.exists():
            marker.unlink()


class FilterPolicyUnitTests(unittest.TestCase):
    def test_resolve_filter_policy_defaults_to_strict(self):
        self.assertEqual(resolve_filter_policy(None), "strict")
        self.assertEqual(resolve_filter_policy("strict"), "strict")
        self.assertEqual(resolve_filter_policy("PERMISSIVE"), "permissive")
        self.assertEqual(
            resolve_filter_policy(None, config={"git_filter_policy": "permissive"}),
            "permissive",
        )

    def test_resolve_filter_policy_rejects_unknown_values(self):
        for bad in ("bogus", "", "warn", "off"):
            with self.assertRaises(ValueError) as caught:
                resolve_filter_policy(bad)
            self.assertIn("git_filter_policy", str(caught.exception))
            self.assertIn("strict", str(caught.exception))
            self.assertIn("permissive", str(caught.exception))

    def test_resolve_filter_policy_prefers_explicit_over_env(self):
        old = os.environ.get("SABLE_GIT_FILTER_POLICY")
        os.environ["SABLE_GIT_FILTER_POLICY"] = "permissive"

        def _restore():
            if old is None:
                os.environ.pop("SABLE_GIT_FILTER_POLICY", None)
            else:
                os.environ["SABLE_GIT_FILTER_POLICY"] = old

        self.addCleanup(_restore)
        self.assertEqual(resolve_filter_policy(None), "permissive")
        self.assertEqual(resolve_filter_policy("strict"), "strict")

    def test_read_only_git_args_classification(self):
        read_only = [
            ["status", "--short"],
            ["diff"],
            ["diff", "--cached", "--name-only"],
            ["log", "--oneline"],
            ["ls-files", "--others"],
            ["rev-parse", "HEAD"],
            ["rev-list", "--count", "HEAD"],
            ["branch", "-a"],
            ["branch", "--show-current"],
            ["remote", "get-url", "origin"],
        ]
        for args in read_only:
            self.assertTrue(is_read_only_git_args(args), args)
        mutating = [
            ["add", "."],
            ["commit", "-m", "x"],
            ["push", "origin", "main"],
            ["pull"],
            ["branch", "feature"],
            ["switch", "main"],
            ["stash", "push"],
            ["remote", "add", "origin", "https://example.invalid/x"],
            ["checkout", "-b", "x"],
            ["merge", "main"],
            ["unknown-subcommand"],
            [],
        ]
        for args in mutating:
            self.assertFalse(is_read_only_git_args(args), args)


class FilterPolicyTests(FilterFixtureMixin, unittest.TestCase):
    def test_plain_git_executes_clean_filter_fixture_is_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_repo(tmp, driver="clean")
            self.assertFalse(self.marker(root))
            _run("git", "-C", str(root), "status", "--short")
            self.assertTrue(self.marker(root), "fixture clean filter did not run under plain git")

    def test_plain_git_executes_smudge_filter_fixture_is_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_repo(tmp, driver="smudge")
            self.assertFalse(self.marker(root))
            _run("git", "-C", str(root), "switch", "other")
            self.assertTrue(self.marker(root), "fixture smudge filter did not run under plain git")

    def test_strict_refuses_non_lfs_clean_filter_on_inspection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_repo(tmp, driver="clean")
            ex = ToolExecutor(str(root))
            result = ex.git_status()
            self.assertFalse(result.success)
            self.assertIn("evil", result.error)
            self.assertIn("strict", result.error)
            self.assertIn("permissive", result.error)
            self.assertFalse(self.marker(root))
            diff = ex.git_diff()
            self.assertFalse(diff.success)
            self.assertIn("evil", diff.error)
            self.assertFalse(self.marker(root))

    def test_strict_refuses_non_lfs_filter_on_mutating_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_repo(tmp, driver="clean")
            ex = ToolExecutor(str(root))
            result = ex.git_add(".")
            self.assertFalse(result.success)
            self.assertIn("evil", result.error)
            self.assertFalse(self.marker(root))

    def test_strict_refuses_smudge_filter_on_branch_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_repo(tmp, driver="smudge")
            ex = ToolExecutor(str(root))
            result = ex.git_branch("other")
            self.assertFalse(result.success)
            self.assertIn("evil", result.error)
            self.assertFalse(self.marker(root))

    def test_strict_allows_lfs_clean_smudge_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_repo(tmp, driver="lfs")
            ex = ToolExecutor(str(root))
            result = ex.git_status()
            self.assertTrue(result.success, result.error)
            # The shim ran: the allowlisted command actually executed.
            self.assertTrue(self.marker(root, "LFS_MARKER"))

    def test_strict_allows_lfs_filter_process_command(self):
        # The policy layer allowlists `git-lfs filter-process`; no Git
        # operation is run here because a fake shim cannot speak git's
        # filter-process pkt-line protocol, and the policy decision is what
        # this test proves — the command is classified, not executed.
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_repo(tmp, driver="process")
            check = check_git_filter_policy(root, policy="strict")
            self.assertTrue(check.allowed, check.message)
            self.assertIsNone(check.warning)

    def test_permissive_warns_and_continues_on_inspection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_repo(tmp, driver="clean")
            ex = ToolExecutor(str(root), filter_policy="permissive")
            result = ex.git_status()
            self.assertTrue(result.success, result.error)
            # The filter ran under the read-only carve-out.
            self.assertTrue(self.marker(root))
            warning = result.data.get("filter_policy_warning", "")
            self.assertIn("evil", warning)
            self.assertIn("permissive", warning)

    def test_permissive_carveout_inspection_runs_but_mutating_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_repo(tmp, driver="clean")
            ex = ToolExecutor(str(root), filter_policy="permissive")
            status = ex.git_status()
            self.assertTrue(status.success, status.error)
            self.assertTrue(self.marker(root))
            self.clear_marker(root)
            result = ex.git_add(".")
            self.assertFalse(result.success)
            self.assertIn("evil", result.error)
            self.assertIn("permissive", result.error)
            self.assertFalse(self.marker(root))

    def test_git_commit_scan_refuses_filter_in_strict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_repo(tmp, driver="clean")
            # Stage with plain git (the test's own doing); the marker this
            # creates is cleared so the commit path is asserted on its own.
            _run("git", "-C", str(root), "add", ".")
            self.clear_marker(root)
            ex = ToolExecutor(str(root))
            result = ex.git_commit("change file")
            self.assertFalse(result.success)
            self.assertIn("Commit aborted", result.error)
            self.assertIn("evil", result.error)
            self.assertFalse(self.marker(root))

    def test_invalid_policy_value_rejected_at_construction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_repo(tmp, driver=None)
            with self.assertRaises(ValueError):
                ToolExecutor(str(root), filter_policy="bogus")
            with self.assertRaises(ValueError):
                check_git_filter_policy(root, policy="bogus")

    def test_env_var_selects_permissive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_repo(tmp, driver="clean")
            old = os.environ.get("SABLE_GIT_FILTER_POLICY")
            os.environ["SABLE_GIT_FILTER_POLICY"] = "permissive"

            def _restore():
                if old is None:
                    os.environ.pop("SABLE_GIT_FILTER_POLICY", None)
                else:
                    os.environ["SABLE_GIT_FILTER_POLICY"] = old

            self.addCleanup(_restore)
            ex = ToolExecutor(str(root))
            result = ex.git_status()
            self.assertTrue(result.success, result.error)
            self.assertIn("permissive", result.data.get("filter_policy_warning", ""))

    def test_include_path_filter_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_repo(tmp, driver=None)
            script = root / "driver.sh"
            script.write_text(f"#!/bin/sh\ntouch {root}/MARKER\n")
            script.chmod(0o755)
            included = root / "included.gitconfig"
            included.write_text(f"[filter \"evil\"]\n\tclean = {script}\n")
            _run("git", "-C", str(root), "config", "include.path", str(included))
            (root / ".gitattributes").write_text("*.bin filter=evil\n")
            (root / "data.bin").write_text("two\n")
            ex = ToolExecutor(str(root))
            result = ex.git_status()
            self.assertFalse(result.success)
            self.assertIn("evil", result.error)
            self.assertFalse(self.marker(root))

    def test_global_config_filter_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_repo(tmp, driver=None)
            script = root / "driver.sh"
            script.write_text(f"#!/bin/sh\ntouch {root}/MARKER\n")
            script.chmod(0o755)
            home = Path(tmp) / "fakehome"
            home.mkdir()
            (home / ".gitconfig").write_text(f"[filter \"evil\"]\n\tclean = {script}\n")
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = str(home)

            def _restore_home():
                if old_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = old_home

            self.addCleanup(_restore_home)
            (root / ".gitattributes").write_text("*.bin filter=evil\n")
            (root / "data.bin").write_text("two\n")
            ex = ToolExecutor(str(root))
            result = ex.git_status()
            self.assertFalse(result.success)
            self.assertIn("evil", result.error)
            self.assertFalse(self.marker(root))

    def test_filter_required_boolean_is_ignored(self):
        """A driver entry with no command cannot execute anything.

        ``filter.evil.required=true`` with no clean/smudge command makes
        git itself fail the operation ("clean filter 'evil' failed") —
        that is git failing closed, not Sable's policy. The policy layer
        must not false-positive on it: the scan only flags clean/smudge/
        process commands, so this configuration is allowed through.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_repo(tmp, driver=None)
            _run("git", "-C", str(root), "config", "filter.evil.required", "true")
            _run("git", "-C", str(root), "config", "filter.evil.clean", "")
            check = check_git_filter_policy(root, policy="strict")
            self.assertTrue(check.allowed, check.message)


class FilterPolicyBypassPathTests(FilterFixtureMixin, unittest.TestCase):
    def test_context_engine_refuses_filter_in_strict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_repo(tmp, driver="clean")
            context = ContextEngine(root).build()
            self.assertTrue(context.git["present"])
            self.assertIn("evil", context.git.get("filter_policy_refusal", ""))
            self.assertIn("strict", context.git.get("filter_policy_refusal", ""))
            self.assertFalse(self.marker(root))

    def test_context_engine_warns_in_permissive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_repo(tmp, driver="clean")
            context = ContextEngine(root, filter_policy="permissive").build()
            self.assertTrue(context.git["present"])
            self.assertIn("evil", context.git.get("filter_policy_warning", ""))
            self.assertTrue(self.marker(root))

    def test_transactions_baseline_refuses_filter_in_strict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_repo(tmp, driver="clean")
            with tempfile.TemporaryDirectory() as store:
                manager = WorkspaceTransactionManager(root, storage_dir=store)
                manager.begin("probe")
            self.assertIn("evil", manager.filter_policy_refusal or "")
            self.assertFalse(self.marker(root))

    def test_doctor_reports_filter_refusal_in_strict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_repo(tmp, driver="clean")
            with tempfile.TemporaryDirectory() as state:
                report = diagnose(
                    root,
                    config_dir=state,
                    config_file=Path(state) / "config.json",
                )
            self.assertFalse(self.marker(root))
            refusal = [
                c
                for c in report.checks
                if c.section == "Git" and c.name == "filter policy"
            ]
            self.assertEqual(len(refusal), 1)
            self.assertEqual(refusal[0].status, "FAIL")
            self.assertIn("evil", refusal[0].detail)

    def test_doctor_warns_in_permissive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_repo(tmp, driver="clean")
            with tempfile.TemporaryDirectory() as state:
                report = diagnose(
                    root,
                    config={"git_filter_policy": "permissive"},
                    config_dir=state,
                    config_file=Path(state) / "config.json",
                )
            warning = [
                c
                for c in report.checks
                if c.section == "Git" and c.name == "filter policy"
            ]
            self.assertEqual(len(warning), 1)
            self.assertEqual(warning[0].status, "WARN")
            self.assertIn("evil", warning[0].detail)
            self.assertTrue(any(c.section == "Git" for c in report.checks))


if __name__ == "__main__":
    unittest.main()
