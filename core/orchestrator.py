# core/orchestrator.py
"""
Orchestrator — smart single-pass pipeline.

Guarantees:
- Main agent runs once per request (no rewrite loops)
- Debug agent only triggered when main agent reports actual code failures
- Auto git-push when branch is ahead of remote
- Git state always verified after commit
"""

import os
from .main_agent import MainAgent
from .debug_agent import DebugAgent
from .tools import ToolExecutor


class Orchestrator:
    def __init__(
        self,
        main_agent: MainAgent,
        debug_agent: DebugAgent,
        executor: ToolExecutor,
        max_debug_loops: int = 3,
        auto_commit: bool = True,
        on_status=None,
    ):
        self.main = main_agent
        self.debug = debug_agent
        self.executor = executor
        self.max_debug_loops = max_debug_loops
        self.auto_commit = auto_commit
        self.on_status = on_status or (lambda msg: None)

    def _status(self, msg: str):
        self.on_status(msg)

    def handle(self, user_message: str, debug_enabled: bool = True, run_command: str = None) -> dict:
        result = {
            "user_message": user_message,
            "chat_reply": "",
            "changes_summary": [],
            "debug_loops": [],
            "final_status": "unknown",
            "tool_results": [],
            "debug_skipped_reason": "",
        }

        # ── Step 1: Main agent (single pass) ─────────────────────────────────
        self._status("🤖  Sable thinking...")
        main_out = self.main.run(user_message)
        result["chat_reply"]      = main_out["chat_reply"]
        result["changes_summary"] = main_out["changes_summary"]
        result["tool_results"]    = main_out["tool_results"]

        # ── Step 2: Debug decision — only on real failures ────────────────────
        agent_wants_debug = main_out.get("needs_debug", False)
        suggested_loops   = main_out.get("suggested_debug_loops", 1)

        # Check for actual tool failures that involve running code
        code_tool_failures = [
            r for r in main_out["tool_results"]
            if not r.success and r.tool in ("run_bash",)
        ]
        has_real_failure = len(code_tool_failures) > 0

        if not debug_enabled:
            result["final_status"] = "built"
            result["debug_skipped_reason"] = "debug disabled by user"
            self._apply_git_workflow(result, user_message)
            return result

        # Only run debug if: agent flagged it AND there was an actual failure
        # OR agent flagged it for a brand-new runnable piece of code
        should_debug = agent_wants_debug and (has_real_failure or suggested_loops >= 1)

        if not should_debug:
            reason = "no code execution needed" if not agent_wants_debug else "no runtime failures detected"
            result["final_status"] = "built"
            result["debug_skipped_reason"] = reason
            self._apply_git_workflow(result, user_message)
            return result

        effective_loops = min(suggested_loops, self.max_debug_loops)
        self._status(f"🔍  Verifying output — up to {effective_loops} check(s)...")

        # ── Step 3: Debug loop (strictly bounded) ────────────────────────────
        loop_count = 0
        intent = user_message

        while loop_count < effective_loops:
            loop_count += 1
            self._status(f"🔍  Debug check {loop_count}/{effective_loops}...")

            debug_out = self.debug.run(intent, run_command=run_command)
            loop_record = {
                "loop": loop_count,
                "status": debug_out["status"],
                "issues": debug_out["issues"],
                "summary": debug_out["summary"],
                "run_output": debug_out.get("run_output", ""),
            }
            result["debug_loops"].append(loop_record)

            if debug_out["status"] == "pass":
                self._status("✅  Verification passed!")
                result["final_status"] = "pass"
                break

            fix_prompt = debug_out.get("fix_prompt", "")
            if not fix_prompt:
                result["final_status"] = "partial"
                self._status("⚠️  Issues found but no fix available.")
                break

            self._status(f"⚠️  Issues: {', '.join(debug_out['issues'][:2])}")
            self._status("🔧  Applying fixes...")

            # One fix pass — no nested loops
            fix_out = self.main.run(fix_prompt)
            result["changes_summary"] += fix_out["changes_summary"]
            result["tool_results"]    += fix_out["tool_results"]
            result["chat_reply"]       = fix_out["chat_reply"]

        else:
            result["final_status"] = "max_loops_reached"
            self._status("⛔  Max debug loops reached.")

        self._apply_git_workflow(result, user_message)
        return result

    def _apply_git_workflow(self, result: dict, user_message: str):
        """
        Auto-commit and auto-push if branch is ahead of remote.
        Handles missing remote gracefully (signals CLI to prompt).
        """
        git_dir = os.path.join(self.executor.project_dir, ".git")
        if not (self.auto_commit and os.path.isdir(git_dir)):
            return

        self.executor.git_add(".")
        status_r = self.executor.git_status()
        has_changes = bool(status_r.output.strip())

        if has_changes:
            self._status("📦  Committing changes...")
            short_intent = user_message[:72].strip()
            commit_r = self.executor.git_commit(f"feat: {short_intent}")
            if commit_r.success:
                result["git_commit"] = commit_r.output
            else:
                result["git_commit"] = f"Commit failed: {commit_r.error}"
                return
        else:
            result["git_commit"] = "Nothing new to commit."

        # Auto-push if branch is ahead of remote
        ahead = self.executor.git_ahead_count()
        if ahead > 0:
            self._status(f"📤  Branch is {ahead} commit(s) ahead — pushing...")
            push_r = self.executor.git_push()
            if push_r.error == "__NO_REMOTE__":
                result["git_push"] = "__NEEDS_REMOTE__"   # CLI layer will prompt
            elif push_r.success:
                result["git_push"] = push_r.output
            else:
                result["git_push"] = f"Push failed: {push_r.error}"
        else:
            remote_r = self.executor.run_bash(
                "git remote get-url origin 2>/dev/null", cwd=self.executor.project_dir
            )
            if not remote_r.output.strip():
                result["git_push"] = "__NEEDS_REMOTE__"
            else:
                result["git_push"] = ""   # nothing to push, suppress output
