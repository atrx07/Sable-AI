import json
import tempfile
import unittest
from pathlib import Path

from sable.main_agent import MainAgent
from sable.orchestrator import Orchestrator
from sable.sessions.manager import SessionManager
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


class AutoCommitHookTests(unittest.TestCase):
    def test_auto_commit_does_not_execute_repo_hooks(self):
        with (
            tempfile.TemporaryDirectory() as root,
            tempfile.TemporaryDirectory() as store,
            tempfile.TemporaryDirectory() as sessions,
        ):
            executor = ToolExecutor(root, transaction_storage_dir=store)
            self.assertTrue(executor.git_init().success)
            executor._git(["config", "user.name", "Sable Test"], "git")
            executor._git(["config", "user.email", "sable@example.invalid"], "git")
            hook = Path(root, ".git", "hooks", "pre-commit")
            hook.write_text("#!/bin/sh\ntouch hook_ran\n")
            hook.chmod(0o755)
            client = _TwoTaskClient()
            agent = MainAgent(client, executor, max_steps=4)
            manager = SessionManager(root, storage_dir=sessions)
            orchestrator = Orchestrator(
                agent, _PassingVerifier(), executor, session_manager=manager
            )
            result = orchestrator.handle("make an advanced calculator")
            self.assertEqual(result["final_status"], "pass")
            self.assertTrue(result.get("commit_sha"), "auto-commit must have created a commit")
            self.assertFalse(Path(root, "hook_ran").exists())


class _WritingMain:
    """Fake agent that makes one file change through the executor."""

    def __init__(self, executor):
        self.executor = executor
        self.history: list = []

    def run(self, _message, mode="build", **_kwargs):
        write = self.executor.write_file("changed.txt", "updated\n")
        return {
            "chat_reply": "done",
            "changes_summary": [],
            "tool_results": [write],
            "changed_files": list(write.changed_files),
            "steps": 1,
            "model_calls": 1,
            "tool_calls": 1,
        }


class _PassingVerifier:
    def verify(self, changed_files, run_command=None, mode="build", scope=None):
        return {
            "status": "pass",
            "overall_status": "PASS",
            "scope": "affected",
            "summary": "PASS: PASS=1.",
            "checks": [
                {
                    "check": {"name": "Python syntax", "category": "SYNTAX"},
                    "status": "PASS",
                }
            ],
        }


class _FailingVerifier:
    def verify(self, changed_files, run_command=None, mode="build", scope=None):
        return {
            "status": "fail",
            "overall_status": "FAIL",
            "scope": "affected",
            "summary": "FAIL: Python syntax check failed.",
            "checks": [
                {
                    "check": {"name": "Python syntax", "category": "SYNTAX"},
                    "status": "FAIL",
                    "diagnostic": "invalid syntax",
                    "failure_signature": "syntax:changed.txt",
                }
            ],
        }


class FailurePathTests(unittest.TestCase):
    def test_verification_failure_appends_outcome_line_digest_and_names_check(self):
        # C R3-F3 probe-G shape: a failed run gets the outcome line (with
        # the real verification dict) and a digest that names the failure,
        # not just the failure sentence.
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as store:
            executor = ToolExecutor(root, transaction_storage_dir=store)
            self.assertTrue(executor.git_init().success)
            agent = _WritingMain(executor)
            orchestrator = Orchestrator(
                agent,
                _FailingVerifier(),
                executor,
                max_fix_loops=0,
                auto_commit=False,
            )
            result = orchestrator.handle("change a file")
            self.assertEqual(result["final_status"], "verification_failed")
            self.assertIn("[RUNTIME]", result["chat_reply"])
            self.assertIn("Failed check(s): Python syntax", result["chat_reply"])
            self.assertIn("Sable did not auto-commit these changes", result["chat_reply"])
            self.assertIn("Verification: FAIL", result["chat_reply"])
            self.assertIn("SYNTAX_ONLY", result["chat_reply"])
            digests = [
                message
                for message in agent.history
                if message["role"] == "system" and "[RUNTIME DIGEST]" in message["content"]
            ]
            self.assertEqual(len(digests), 1, "failure path appends exactly one digest")
            self.assertIn("verification: FAIL", digests[0]["content"])
            self.assertIn("not committed", digests[0]["content"])

    def test_blocked_path_appends_outcome_line_and_digest(self):
        # C R3-F3: a provider failure inside the agent run is a blocked
        # return path — it gets the outcome line and digest too.
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as store:
            executor = ToolExecutor(root, transaction_storage_dir=store)
            self.assertTrue(executor.git_init().success)
            agent = MainAgent(_FailingProviderClient(), executor, max_steps=4)
            orchestrator = Orchestrator(agent, _PassingVerifier(), executor, auto_commit=False)
            result = orchestrator.handle("change a file")
            self.assertEqual(result["final_status"], "blocked")
            self.assertIn("[RUNTIME]", result["chat_reply"])
            # Verification ran on the changed file before the provider
            # failure stopped the run, so the outcome line reports it.
            self.assertIn("Verification: PASS", result["chat_reply"])
            self.assertIn("Git: no commit created", result["chat_reply"])
            digests = [
                message
                for message in agent.history
                if message["role"] == "system" and "[RUNTIME DIGEST]" in message["content"]
            ]
            self.assertTrue(digests, "blocked path appends a digest")
            self.assertIn("not committed", digests[-1]["content"])


class OutcomeLineTests(unittest.TestCase):
    def test_successful_run_appends_runtime_outcome_line(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as store:
            executor = ToolExecutor(root, transaction_storage_dir=store)
            self.assertTrue(executor.git_init().success)
            orchestrator = Orchestrator(
                _WritingMain(executor),
                _PassingVerifier(),
                executor,
                auto_commit=False,
            )
            result = orchestrator.handle("change a file")
            self.assertEqual(result["final_status"], "pass")
            self.assertIn("[RUNTIME]", result["chat_reply"])
            self.assertIn("Verification: PASS", result["chat_reply"])
            self.assertIn("SYNTAX_ONLY", result["chat_reply"])
            self.assertIn("syntax only; no tests ran", result["chat_reply"])
            self.assertIn("Git: no commit created", result["chat_reply"])
            self.assertIn("Policy: build mode", result["chat_reply"])
            self.assertIn("not requestable in build mode", result["chat_reply"])

    def test_plan_mode_appends_runtime_git_line(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as store:
            executor = ToolExecutor(root, transaction_storage_dir=store)
            self.assertTrue(executor.git_init().success)
            orchestrator = Orchestrator(
                _WritingMain(executor),
                _PassingVerifier(),
                executor,
                auto_commit=False,
            )
            result = orchestrator.handle("ready to push to github?", mode="plan")
            self.assertEqual(result["final_status"], "plan")
            self.assertIn("[RUNTIME]", result["chat_reply"])
            self.assertIn("Verification: not run", result["chat_reply"])
            self.assertIn("branch: main", result["chat_reply"])
            self.assertIn("Policy: plan mode is read-only", result["chat_reply"])


class _TwoTaskClient:
    """Call 1 writes calculator.py, call 2 reports it, later calls answer plainly."""

    def __init__(self):
        self.calls = 0
        self.seen_messages = []

    def complete(self, messages, **kwargs):
        self.calls += 1
        self.seen_messages.append(list(messages))
        if self.calls == 1:
            return {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps(
                                {
                                    "path": "calculator.py",
                                    "content": "def add(a, b):\n    return a + b\n",
                                }
                            ),
                        },
                    }
                ],
            }
        if self.calls == 2:
            return {"content": "Created calculator.py.", "tool_calls": []}
        return {"content": "Yes, it was committed.", "tool_calls": []}


class _PlainAnswerClient:
    """Answers without tools; used for turns with no verification/commit."""

    def __init__(self):
        self.calls = 0
        self.seen_messages = []

    def complete(self, messages, **kwargs):
        self.calls += 1
        self.seen_messages.append(list(messages))
        return {"content": "Hello.", "tool_calls": []}


class SessionRecapTests(unittest.TestCase):
    def test_recap_carries_commit_sha_to_next_task(self):
        with (
            tempfile.TemporaryDirectory() as root,
            tempfile.TemporaryDirectory() as store,
            tempfile.TemporaryDirectory() as sessions,
        ):
            executor = ToolExecutor(root, transaction_storage_dir=store)
            self.assertTrue(executor.git_init().success)
            executor._git(["config", "user.name", "Sable Test"], "git")
            executor._git(["config", "user.email", "sable@example.invalid"], "git")
            client = _TwoTaskClient()
            agent = MainAgent(client, executor, max_steps=4)
            manager = SessionManager(root, storage_dir=sessions)
            orchestrator = Orchestrator(
                agent, _PassingVerifier(), executor, session_manager=manager
            )
            first = orchestrator.handle("make an advanced calculator")
            self.assertEqual(first["final_status"], "pass")
            commit_sha = first.get("commit_sha")
            self.assertTrue(commit_sha, "auto-commit must record the commit sha")
            self.assertEqual(manager.current.latest_task["commit_sha"], commit_sha)
            digests = [
                message
                for message in agent.history
                if message["role"] == "system" and "committed as" in message["content"]
            ]
            self.assertTrue(digests, "history must record the auto-commit outcome")
            self.assertIn(commit_sha[:12], digests[-1]["content"])
            second = orchestrator.handle("did you commit calculator.py?")
            # No files changed in the question turn, so no verification runs.
            self.assertEqual(second["final_status"], "built")
            joined = "\n".join(str(m.get("content") or "") for m in client.seen_messages[2])
            self.assertIn("Previous task", joined)
            self.assertIn(commit_sha[:12], joined)
            self.assertIn("Previous task", second["evidence_summary"])
            self.assertIn(commit_sha[:12], second["evidence_summary"])

    def test_recap_reports_not_run_and_not_committed(self):
        # C R3-F5: a previous task with no verification record and no
        # commit recaps as "verification: not run" / "not committed",
        # never "UNKNOWN" and never silence.
        with (
            tempfile.TemporaryDirectory() as root,
            tempfile.TemporaryDirectory() as store,
            tempfile.TemporaryDirectory() as sessions,
        ):
            executor = ToolExecutor(root, transaction_storage_dir=store)
            self.assertTrue(executor.git_init().success)
            client = _PlainAnswerClient()
            agent = MainAgent(client, executor, max_steps=4)
            manager = SessionManager(root, storage_dir=sessions)
            orchestrator = Orchestrator(
                agent, _PassingVerifier(), executor, session_manager=manager
            )
            first = orchestrator.handle("hello")
            self.assertEqual(first["final_status"], "built")
            self.assertIsNone(first.get("commit_sha"))
            second = orchestrator.handle("what did you do last time?")
            self.assertEqual(second["final_status"], "built")
            joined = "\n".join(str(m.get("content") or "") for m in client.seen_messages[1])
            self.assertIn("Previous task", joined)
            self.assertIn("verification: not run", joined)
            self.assertNotIn("verification: UNKNOWN", joined)
            self.assertIn("not committed", joined)


class _FailingProviderClient:
    """First turn writes a file; the next provider call raises."""

    def __init__(self):
        self.calls = 0

    def complete(self, messages, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps({"path": "a.txt", "content": "A"}),
                        },
                    }
                ],
            }
        raise RuntimeError("provider unavailable: 503 Service Unavailable")


class _InterruptingProviderClient:
    """First turn writes a file; the next provider call is interrupted."""

    def __init__(self):
        self.calls = 0

    def complete(self, messages, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps({"path": "a.txt", "content": "A"}),
                        },
                    }
                ],
            }
        raise KeyboardInterrupt


class AgentFailureOrchestratorTests(unittest.TestCase):
    def _repo(self, root, store):
        executor = ToolExecutor(root, transaction_storage_dir=store)
        self.assertTrue(executor.git_init().success)
        executor._git(["config", "user.name", "Sable Test"], "git")
        executor._git(["config", "user.email", "sable@example.invalid"], "git")
        return executor

    def test_provider_failure_blocks_auto_commit(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as store:
            executor = self._repo(root, store)
            agent = MainAgent(_FailingProviderClient(), executor, max_steps=4)
            orchestrator = Orchestrator(agent, _PassingVerifier(), executor)
            result = orchestrator.handle("create a file")
            self.assertEqual(result["final_status"], "blocked")
            self.assertTrue(result["agent_failed"])
            self.assertIn("[RUNTIME] The run stopped before finishing", result["chat_reply"])
            self.assertIn("a.txt", result["changed_files"])
            self.assertIsNone(result.get("commit_sha"))
            self.assertTrue(result.get("undo_available"))

    def test_interrupt_cancels_without_commit(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as store:
            executor = self._repo(root, store)
            agent = MainAgent(_InterruptingProviderClient(), executor, max_steps=4)
            orchestrator = Orchestrator(agent, _PassingVerifier(), executor)
            result = orchestrator.handle("create a file")
            self.assertEqual(result["final_status"], "cancelled")
            self.assertIn("Task cancelled by the user", result["chat_reply"])
            self.assertIn("a.txt", result["changed_files"])
            self.assertIsNone(result.get("commit_sha"))


class _WeakeningTestClient:
    """Call 1 blanket-skips tests/test_app.py, call 2 reports it.

    delete_file is not used: DELETE_WORKSPACE needs human approval, so the
    realistic first-pass weakening is a rewrite that neuters the tests.
    """

    def __init__(self):
        self.calls = 0

    def complete(self, messages, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps(
                                {
                                    "path": "tests/test_app.py",
                                    "content": (
                                        "import pytest\n\n"
                                        'pytestmark = pytest.mark.skip(reason="flaky")\n\n\n'
                                        "def test_add():\n    assert 1 + 1 == 2\n\n\n"
                                        "def test_sub():\n    assert 1 - 1 == 0\n"
                                    ),
                                }
                            ),
                        },
                    }
                ],
            }
        return {"content": "Skipped the flaky tests.", "tool_calls": []}


class InitialPassIntegrityTests(unittest.TestCase):
    def test_first_pass_test_weakening_blocks_auto_commit(self):
        with (
            tempfile.TemporaryDirectory() as root,
            tempfile.TemporaryDirectory() as store,
            tempfile.TemporaryDirectory() as sessions,
        ):
            executor = ToolExecutor(root, transaction_storage_dir=store)
            self.assertTrue(executor.git_init().success)
            executor._git(["config", "user.name", "Sable Test"], "git")
            executor._git(["config", "user.email", "sable@example.invalid"], "git")
            test_dir = Path(root, "tests")
            test_dir.mkdir()
            (test_dir / "test_app.py").write_text(
                "def test_add():\n    assert 1 + 1 == 2\n\n\ndef test_sub():\n    assert 1 - 1 == 0\n"
            )
            executor._git(["add", "tests/test_app.py"], "git")
            executor._git(["commit", "-m", "baseline tests"], "git")
            agent = MainAgent(_WeakeningTestClient(), executor, max_steps=4)
            manager = SessionManager(root, storage_dir=sessions)
            orchestrator = Orchestrator(
                agent, _PassingVerifier(), executor, session_manager=manager
            )
            result = orchestrator.handle("fix the app")
            self.assertEqual(result["final_status"], "verification_integrity_blocked")
            self.assertTrue(result["verification_loops"][-1].get("integrity_blocked"))
            self.assertIn("BLOCKED", result["verification_loops"][-1].get("summary", ""))
            self.assertIn("integrity", result["chat_reply"].lower())
            self.assertFalse(result.get("commit_sha"), "no auto-commit on an integrity flag")
            # C R3-F3: the integrity-blocked return path also gets the
            # outcome line and the orchestrator digest.
            self.assertIn("[RUNTIME]", result["chat_reply"])
            self.assertIn("Verification: BLOCKED", result["chat_reply"])
            digests = [
                message
                for message in agent.history
                if message["role"] == "system" and "[RUNTIME DIGEST]" in message["content"]
            ]
            self.assertEqual(len(digests), 2, "run digest + failure-path digest")
            self.assertIn("verification: BLOCKED", digests[-1]["content"])
            self.assertIn("not committed", digests[-1]["content"])
            log = executor._git(["log", "--oneline"], "git")
            self.assertEqual(len(log.output.strip().splitlines()), 1)
