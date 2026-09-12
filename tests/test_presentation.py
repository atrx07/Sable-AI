import io
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from sable.capabilities import ActionSource, ApprovalDecision, Capability, CapabilityRequest
from sable.orchestrator import Orchestrator
from sable.presentation import PlainRenderer, TerminalRenderer, create_renderer
from sable.runtime import RuntimeEventType, RuntimeTask
from sable.tools import ToolExecutor, ToolResult


class TTYBuffer(io.StringIO):
    encoding = "utf-8"

    def isatty(self):
        return True


def request(action="git push origin main"):
    return CapabilityRequest(
        request_id="cap-test",
        capability=Capability.GIT_PUBLISH,
        action=action,
        source=ActionSource.MODEL,
        task_id="task-test",
        session_id="session-test",
        tool="git_push",
        risk="high",
        reason="Publish the verified changes requested by the user.",
        target="origin/main",
        created_at=datetime.now(timezone.utc).isoformat(),
        scope="git_push:test",
    )


class PresentationTests(unittest.TestCase):
    def render(self, result, *, verification_enabled=True, width=88, verbose=False):
        output = io.StringIO()
        PlainRenderer(stream=output, width=width, verbose=verbose).render_result(
            result, verification_enabled=verification_enabled
        )
        return output.getvalue()

    def test_final_report_uses_structured_runtime_transaction_and_git_data(self):
        result = {
            "final_status": "pass",
            "chat_reply": "Fixed the race and added coverage.",
            "changed_files": ["sable/auth.py", "tests/test_auth.py"],
            "tool_results": [ToolResult("write_file", True, output="secret-free", changed_files=["sable/auth.py"])],
            "verification_loops": [{
                "overall_status": "PASS_WITH_OPTIONAL_SKIPS",
                "scope": "AFFECTED",
                "stage": "final",
                "summary": "Required checks passed; optional checker unavailable.",
                "checks": [{
                    "check": {"name": "pytest · 14 tests"},
                    "status": "PASS",
                    "classification": "NONE",
                    "result": {"duration_ms": 28},
                }, {
                    "check": {"name": "Pyright"},
                    "status": "SKIPPED_UNAVAILABLE",
                    "classification": "ENVIRONMENT_UNAVAILABLE",
                    "diagnostic": "Executable not installed",
                    "result": {"duration_ms": 0},
                }],
            }],
            "runtime_task": {"model_turn_count": 3, "tool_call_count": 4, "duration_ms": 2140},
            "transaction_id": "txn-123",
            "undo_available": True,
            "commit_sha": "abc1234",
        }
        text = self.render(result)
        self.assertIn("COMPLETED · VERIFIED", text)
        self.assertIn("sable/auth.py", text)
        self.assertIn("PASS_WITH_OPTIONAL_SKIPS", text)
        self.assertIn("SKIPPED_UNAVAILABLE", text)
        self.assertIn("3 model turn(s) · 4 tool call(s) · 2.1s", text)
        self.assertIn("txn-123", text)
        self.assertIn("available with /undo", text)
        self.assertIn("abc1234", text)

    def test_all_verification_states_remain_distinct(self):
        for state in ("PASS", "PASS_WITH_OPTIONAL_SKIPS", "FAIL", "INCOMPLETE", "BLOCKED", "SKIPPED"):
            with self.subTest(state=state):
                text = self.render({
                    "final_status": "pass" if state.startswith("PASS") else "verification_incomplete",
                    "verification_loops": [{"overall_status": state, "checks": []}],
                })
                self.assertIn(state, text)

    def test_disabled_verification_is_explicitly_unverified(self):
        text = self.render({"final_status": "built", "chat_reply": "Done."}, verification_enabled=False)
        self.assertIn("UNVERIFIED", text)
        self.assertNotIn("VERIFIED\n", text.replace("UNVERIFIED", ""))

    def test_repair_rollback_and_conflicts_are_obvious(self):
        output = io.StringIO()
        renderer = PlainRenderer(stream=output, error_stream=output)
        task = RuntimeTask.create("repair", tempfile.gettempdir())
        task.event_handler = renderer.on_event
        task.start()
        task.emit_event(RuntimeEventType.REPAIR_STARTED, loop=1)
        task.emit_event(RuntimeEventType.ROLLBACK, success=False)
        renderer.render_result({
            "final_status": "aborted",
            "changed_files": ["partial.py"],
            "transaction_id": "txn-recovery",
            "rollback": {"success": False, "error": "conflict"},
            "transaction_conflicts": ["partial.py"],
        })
        text = output.getvalue()
        self.assertIn("[repair] Attempt 1", text)
        self.assertIn("[rollback CONFLICT]", text)
        self.assertIn("CONFLICT OR FAILURE", text)
        self.assertIn("[CONFLICT]  partial.py", text)

    def test_long_paths_wrap_at_narrow_width_and_empty_sections_are_omitted(self):
        path = "very-long-directory-name/" * 5 + "module.py"
        text = self.render({"final_status": "built", "changed_files": [path]}, width=34)
        self.assertIn("Changed", text)
        self.assertNotIn("Verification\n", text)
        self.assertNotIn("Runtime\n", text)
        self.assertTrue(all(len(line) <= 34 for line in text.splitlines()))

    def test_normal_and_verbose_output_redact_secrets(self):
        secret = "gsk_abcdefghijklmnopqrstuvwxyz"
        result = {
            "final_status": "aborted",
            "chat_reply": f"provider rejected {secret}",
            "tool_results": [ToolResult("run_command", False, error=f"token={secret}")],
        }
        for verbose in (False, True):
            with self.subTest(verbose=verbose):
                text = self.render(result, verbose=verbose)
                self.assertNotIn(secret, text)
                self.assertIn("[REDACTED]", text)

    def test_live_events_are_ordered_and_observer_failure_is_nonfatal(self):
        output = io.StringIO()
        renderer = PlainRenderer(stream=output, error_stream=output)
        task = RuntimeTask.create("inspect", tempfile.gettempdir())
        task.event_handler = renderer.on_event
        task.start()
        task.transition(task.current_phase.__class__.CONTEXT, reason="ready")
        task.emit_event(RuntimeEventType.CONTEXT_SELECTED, files_selected=2, files_considered=8)
        task.transition(task.current_phase.__class__.REPORT, reason="done")
        text = output.getvalue()
        self.assertLess(text.index("Discover"), text.index("Context"))
        self.assertLess(text.index("Context"), text.index("Report"))
        self.assertIn("2 files selected from 8", text)

        task = RuntimeTask.create("safe observer", tempfile.gettempdir())
        task.event_handler = lambda _event: (_ for _ in ()).throw(RuntimeError("display failed"))
        task.start()
        self.assertEqual(task.events[0].event_type, RuntimeEventType.TASK_STARTED)

    def test_tool_events_show_compact_default_and_bounded_failure(self):
        output = io.StringIO()
        renderer = PlainRenderer(stream=output, error_stream=output, width=48)
        task = RuntimeTask.create("tools", tempfile.gettempdir())
        task.event_handler = renderer.on_event
        task.start()
        task.emit_event(RuntimeEventType.TOOL_RESULT, tool="read_file", success=True)
        task.emit_event(
            RuntimeEventType.TOOL_RESULT,
            tool="run_command",
            success=False,
            error_excerpt="assertion failed " * 40,
        )
        text = output.getvalue()
        self.assertIn("[tool OK] read_file", text)
        self.assertIn("[tool FAIL] run_command", text)
        self.assertLess(len(text), 400)


class ApprovalPresentationTests(unittest.TestCase):
    def decide(self, answers):
        output = io.StringIO()
        iterator = iter(answers)
        renderer = PlainRenderer(stream=output, input_func=lambda _prompt: next(iterator))
        return renderer.prompt_approval(request()), output.getvalue()

    def test_allow_once_session_and_deny(self):
        self.assertEqual(self.decide(["a"])[0], ApprovalDecision.ALLOW_ONCE)
        self.assertEqual(self.decide(["session"])[0], ApprovalDecision.ALLOW_SESSION)
        self.assertEqual(self.decide([""])[0], ApprovalDecision.DENY)

    def test_invalid_input_reprompts_without_approving(self):
        decision, text = self.decide(["maybe", "d"])
        self.assertEqual(decision, ApprovalDecision.DENY)
        self.assertIn("remains denied by default", text)

    def test_ctrl_c_and_eof_deny(self):
        for error in (KeyboardInterrupt(), EOFError()):
            with self.subTest(error=type(error).__name__):
                renderer = PlainRenderer(stream=io.StringIO(), input_func=lambda _prompt: (_ for _ in ()).throw(error))
                self.assertEqual(renderer.prompt_approval(request()), ApprovalDecision.DENY)

    def test_request_output_is_redacted(self):
        secret = "gsk_abcdefghijklmnopqrstuvwxyz"
        output = io.StringIO()
        renderer = PlainRenderer(stream=output, input_func=lambda _prompt: "d")
        renderer.prompt_approval(request(f"git push https://user:{secret}@example.test/repo"))
        self.assertNotIn(secret, output.getvalue())
        self.assertIn("[REDACTED]", output.getvalue())


class TerminalCapabilityTests(unittest.TestCase):
    def test_color_requires_tty_and_respects_all_disablers(self):
        with patch.dict(os.environ, {"TERM": "xterm-256color"}, clear=False):
            os.environ.pop("NO_COLOR", None)
            self.assertTrue(TerminalRenderer(stream=TTYBuffer()).color_enabled)
            self.assertFalse(TerminalRenderer(stream=io.StringIO()).color_enabled)
            self.assertFalse(TerminalRenderer(stream=TTYBuffer(), color=False).color_enabled)
            self.assertFalse(create_renderer(stream=TTYBuffer(), plain=True).color_enabled)
        with patch.dict(os.environ, {"NO_COLOR": "1", "TERM": "xterm"}, clear=False):
            self.assertFalse(TerminalRenderer(stream=TTYBuffer()).color_enabled)
        with patch.dict(os.environ, {"TERM": "dumb"}, clear=False):
            os.environ.pop("NO_COLOR", None)
            self.assertFalse(TerminalRenderer(stream=TTYBuffer()).color_enabled)


class EventSubscriptionIntegrationTests(unittest.TestCase):
    def test_orchestrator_subscribes_renderer_to_runtime_events(self):
        class Main:
            router = None

            def run(self, _message, mode="build"):
                return {
                    "chat_reply": "done", "changes_summary": [], "tool_results": [],
                    "changed_files": [], "steps": 1, "tool_calls": 0,
                }

        class Verifier:
            def verify(self, changed_files, run_command=None, mode="build"):
                return {"status": "skipped", "overall_status": "SKIPPED", "checks": []}

        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as store:
            executor = ToolExecutor(root, transaction_storage_dir=Path(store))
            events = []
            result = Orchestrator(
                Main(), Verifier(), executor, auto_commit=False, on_event=events.append
            ).handle("inspect repository")
        self.assertEqual(result["final_status"], "built")
        self.assertEqual(events[0].event_type, RuntimeEventType.TASK_STARTED)
        self.assertEqual(events[-1].event_type, RuntimeEventType.TASK_COMPLETED)
        self.assertIn(RuntimeEventType.PHASE_CHANGED, [item.event_type for item in events])


if __name__ == "__main__":
    unittest.main()
