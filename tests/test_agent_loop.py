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
