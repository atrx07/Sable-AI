import json
import tempfile
import unittest
from pathlib import Path

from sable.main_agent import MainAgent
from sable.tools import ToolExecutor


class FakeClient:
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
                            "name": "read_file",
                            "arguments": json.dumps({"path": "a.txt"}),
                        },
                    }
                ],
            }
        if self.calls == 2:
            tool_messages = [m for m in messages if m.get("role") == "tool"]
            self.seen_tool_output = tool_messages[-1]["content"]
            return {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {
                            "name": "patch_file",
                            "arguments": json.dumps({"path": "a.txt", "old": "old", "new": "new"}),
                        },
                    }
                ],
            }
        return {"content": "Read the file first, then patched it.", "tool_calls": []}


class ParallelToolClient:
    def __init__(self, root: str):
        self.calls = 0
        self.root = Path(root)
        self.second_existed_before_reissue = None
        self.saw_deferred_result = False

    def complete(self, messages, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_a",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps({"path": "a.txt", "content": "A"}),
                        },
                    },
                    {
                        "id": "call_b",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps({"path": "b.txt", "content": "B"}),
                        },
                    },
                ],
            }
        if self.calls == 2:
            self.second_existed_before_reissue = (self.root / "b.txt").exists()
            tool_messages = [m for m in messages if m.get("role") == "tool"]
            self.saw_deferred_result = any(
                "Deferred by Sable runtime" in m.get("content", "") for m in tool_messages
            )
            return {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_b_retry",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps({"path": "b.txt", "content": "B"}),
                        },
                    }
                ],
            }
        return {"content": "Applied both writes sequentially.", "tool_calls": []}


class ToolBudgetClient:
    def __init__(self):
        self.calls = 0

    def complete(self, messages, **kwargs):
        self.calls += 1
        if kwargs.get("tool_choice") == "none":
            return {"content": "Stopped at the tool budget.", "tool_calls": []}
        return {
            "content": None,
            "tool_calls": [
                {
                    "id": f"call_{self.calls}",
                    "type": "function",
                    "function": {"name": "list_files", "arguments": json.dumps({"path": "."})},
                }
            ],
        }


class AgentLoopTests(unittest.TestCase):
    def test_tool_result_is_seen_before_dependent_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "a.txt").write_text("old")
            client = FakeClient()
            agent = MainAgent(client, ToolExecutor(tmp), max_steps=4)
            result = agent.run("change old to new", mode="build")
            self.assertIn("old", client.seen_tool_output)
            self.assertEqual(Path(tmp, "a.txt").read_text(), "new")
            self.assertEqual(client.calls, 3)
            self.assertIn("a.txt", result["changed_files"])

    def test_parallel_tool_calls_are_deferred_and_reconsidered(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = ParallelToolClient(tmp)
            agent = MainAgent(client, ToolExecutor(tmp), max_steps=5, max_tool_calls=5)

            result = agent.run("create a and b", mode="build")

            self.assertTrue(Path(tmp, "a.txt").exists())
            self.assertTrue(Path(tmp, "b.txt").exists())
            self.assertFalse(client.second_existed_before_reissue)
            self.assertTrue(client.saw_deferred_result)
            self.assertEqual(result["tool_calls"], 2)

    def test_tool_call_budget_halts_further_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = ToolBudgetClient()
            agent = MainAgent(client, ToolExecutor(tmp), max_steps=5, max_tool_calls=1)

            result = agent.run("keep inspecting", mode="plan")

            self.assertEqual(result["tool_calls"], 1)
            self.assertTrue(result["tool_limit_reached"])
            self.assertIn("Stopped at the tool budget", result["chat_reply"])


class LyingStagingClient:
    """Incident B shape: answers a git-state question with zero tool calls."""

    def __init__(self):
        self.calls = 0
        self.seen_messages: list[dict[str, object]] = []

    def complete(self, messages, **kwargs):
        self.calls += 1
        self.seen_messages = list(messages)
        return {
            "content": "Your changes are staged locally and ready to commit and push.",
            "tool_calls": [],
        }


class DigestClient:
    """Records every message list; first run reads+patch a.txt, later runs plain text."""

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
                            "name": "read_file",
                            "arguments": json.dumps({"path": "a.txt"}),
                        },
                    }
                ],
            }
        if self.calls == 2:
            return {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {
                            "name": "patch_file",
                            "arguments": json.dumps({"path": "a.txt", "old": "old", "new": "new"}),
                        },
                    }
                ],
            }
        return {"content": "Done.", "tool_calls": []}


class EvidenceInjectionTests(unittest.TestCase):
    def test_zero_tool_turn_still_sees_runtime_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = ToolExecutor(tmp)
            self.assertTrue(executor.git_init().success)
            Path(tmp, "calculator.py").write_text("def add(a, b):\n    return a + b\n")
            client = LyingStagingClient()
            agent = MainAgent(client, executor, max_steps=4)
            result = agent.run("ready to push to github?", mode="build")
            # The model's prose is unchanged; the runtime evidence must sit beside it.
            self.assertEqual(
                result["chat_reply"],
                "Your changes are staged locally and ready to commit and push.",
            )
            evidence_messages = [
                message
                for message in client.seen_messages
                if message.get("role") == "system"
                and "RUNTIME EVIDENCE" in str(message.get("content", ""))
            ]
            self.assertTrue(evidence_messages, "no runtime evidence block was injected")
            block = str(evidence_messages[-1]["content"])
            self.assertIn("calculator.py", block)
            self.assertIn("untracked: [calculator.py]", block)
            self.assertIn("staged: []", block)
            self.assertIn("branch: main", block)
            self.assertIn("tools executed this turn: none", block)
            self.assertIn("RUNTIME EVIDENCE", result["evidence_summary"])
            self.assertIn("untracked: [calculator.py]", result["evidence_summary"])

    def test_evidence_block_lists_tool_outcomes_across_turns(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "a.txt").write_text("old")
            client = FakeClient()
            agent = MainAgent(client, ToolExecutor(tmp), max_steps=4)
            result = agent.run("change old to new", mode="build")
            block = result["evidence_summary"]
            self.assertIn("read_file(ok)", block)
            self.assertIn("patch_file(ok)", block)
            self.assertIn("files changed this turn: a.txt", block)


class HistoryDepthTests(unittest.TestCase):
    def _agent_with_history(self, tmp, turns):
        from sable.main_agent import MainAgent

        agent = MainAgent(DigestClient(), ToolExecutor(tmp), max_steps=4)
        # Four entries per turn: user, assistant, run digest, orchestrator
        # digest on commit turns — the maximum a turn appends.
        for turn in range(turns):
            for slot in ("user", "assistant", "digest", "orchestrator-digest"):
                agent.history.append({"role": "user", "content": f"turn-{turn}-{slot}"})
        return agent

    def test_window_holds_eight_full_turns(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = self._agent_with_history(tmp, 8)
            messages = agent._base_messages("build", "ctx")
            joined = "\n".join(str(item.get("content") or "") for item in messages)
            for turn in range(8):
                self.assertIn(f"turn-{turn}-user", joined)
                self.assertIn(f"turn-{turn}-orchestrator-digest", joined)

    def test_window_drops_turns_older_than_eight(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = self._agent_with_history(tmp, 9)
            messages = agent._base_messages("build", "ctx")
            joined = "\n".join(str(item.get("content") or "") for item in messages)
            self.assertNotIn("turn-0-user", joined)
            self.assertIn("turn-8-user", joined)


class HistoryDigestTests(unittest.TestCase):
    def test_digest_reenters_context_on_next_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "a.txt").write_text("old")
            client = DigestClient()
            agent = MainAgent(client, ToolExecutor(tmp), max_steps=4)
            result = agent.run("change old to new", mode="build")
            self.assertIn("a.txt", result["changed_files"])
            digests = [
                message
                for message in agent.history
                if message["role"] == "system" and "[RUNTIME DIGEST]" in message["content"]
            ]
            self.assertEqual(len(digests), 1, "one digest per turn")
            self.assertIn("a.txt", digests[0]["content"])
            self.assertIn("files changed: a.txt", digests[0]["content"])
            # Second turn: the digest is replayed to the model as history.
            agent.run("was a.txt changed?")
            replay = client.seen_messages[3]
            joined = "\n".join(str(m.get("content") or "") for m in replay)
            self.assertIn("[RUNTIME DIGEST]", joined)
            self.assertIn("a.txt", joined)
            self.assertIn("re-inspect before asserting current state", joined)

    def test_digest_marks_verification_not_run_yet(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = DigestClient()
            agent = MainAgent(client, ToolExecutor(tmp), max_steps=4)
            agent.run("hello")
            digests = [
                message
                for message in agent.history
                if message["role"] == "system" and "[RUNTIME DIGEST]" in message["content"]
            ]
            self.assertEqual(len(digests), 1)
            self.assertIn("verification: not run yet", digests[0]["content"])
            self.assertIn("files changed: none", digests[0]["content"])


class PromptTextTests(unittest.TestCase):
    def _descriptions(self):
        from sable.tool_schemas import TOOL_SCHEMAS

        return {item["function"]["name"]: item["function"]["description"] for item in TOOL_SCHEMAS}

    def test_git_schemas_state_their_limits(self):
        descriptions = self._descriptions()
        self.assertIn(
            "says nothing about whether anything was pushed",
            descriptions["git_status"],
        )
        self.assertIn(
            "a clean git_status does not mean a push happened",
            descriptions["git_push"],
        )
        self.assertIn("missing-remote error", descriptions["git_push"])
        # A R1-F3: git_commit must not imply it stages, and recent_changes
        # must state its readiness-payload role.
        self.assertIn(
            "Commits only already-staged work",
            descriptions["git_commit"],
        )
        self.assertIn("use /git add to stage", descriptions["git_commit"])
        self.assertIn("Readiness payload", descriptions["recent_changes"])
        self.assertIn("reports state, it never changes it", descriptions["recent_changes"])

    def test_base_prompt_has_state_assertion_rule(self):
        from sable.main_agent import BASE_SYSTEM_PROMPT

        self.assertIn(
            "Assert git state, staging, commits, pushes, and verification", BASE_SYSTEM_PROMPT
        )
        self.assertIn("re-inspect first", BASE_SYSTEM_PROMPT)
        self.assertIn("If no tool proved it, say so", BASE_SYSTEM_PROMPT)


class FailingProviderClient:
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


class AgentFailureReportTests(unittest.TestCase):
    def test_provider_failure_yields_runtime_report_not_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FailingProviderClient()
            agent = MainAgent(client, ToolExecutor(tmp), max_steps=4)
            result = agent.run("create a file", mode="build")
            self.assertTrue(result["agent_failed"])
            self.assertEqual(result["agent_failure"]["kind"], "provider_error")
            self.assertIn("a.txt", result["changed_files"])
            reply = result["chat_reply"]
            self.assertTrue(reply.startswith("[RUNTIME]"))
            self.assertIn("The run stopped before finishing", reply)
            self.assertIn("Done:", reply)
            self.assertIn("Not done:", reply)
            self.assertIn("Reversible:", reply)
            self.assertIn("a.txt", reply)
            self.assertNotEqual(reply.strip(), "Done.")

    def test_failure_report_redacts_secrets(self):
        from sable.runtime import build_failure_report

        report = build_failure_report(
            kind="provider_error",
            error="boom gsk_abcdefghijklmnopqrstuvwxyz",
            tool_results=[],
            changed_files=[],
        )
        self.assertNotIn("gsk_abcdefghijklmnopqrstuvwxyz", report)
        self.assertIn("nothing was changed", report.lower())
