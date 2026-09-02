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
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": json.dumps({"path": "a.txt"})},
                }],
            }
        if self.calls == 2:
            tool_messages = [m for m in messages if m.get("role") == "tool"]
            self.seen_tool_output = tool_messages[-1]["content"]
            return {
                "content": None,
                "tool_calls": [{
                    "id": "call_2",
                    "type": "function",
                    "function": {"name": "patch_file", "arguments": json.dumps({"path": "a.txt", "old": "old", "new": "new"})},
                }],
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
                        "function": {"name": "write_file", "arguments": json.dumps({"path": "a.txt", "content": "A"})},
                    },
                    {
                        "id": "call_b",
                        "type": "function",
                        "function": {"name": "write_file", "arguments": json.dumps({"path": "b.txt", "content": "B"})},
                    },
                ],
            }
        if self.calls == 2:
            self.second_existed_before_reissue = (self.root / "b.txt").exists()
            tool_messages = [m for m in messages if m.get("role") == "tool"]
            self.saw_deferred_result = any("Deferred by Sable runtime" in m.get("content", "") for m in tool_messages)
            return {
                "content": None,
                "tool_calls": [{
                    "id": "call_b_retry",
                    "type": "function",
                    "function": {"name": "write_file", "arguments": json.dumps({"path": "b.txt", "content": "B"})},
                }],
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
            "tool_calls": [{
                "id": f"call_{self.calls}",
                "type": "function",
                "function": {"name": "list_files", "arguments": json.dumps({"path": "."})},
            }],
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
