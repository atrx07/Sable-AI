"""Sable v2 orchestration: agent loop -> deterministic verifier -> bounded fixes -> git."""

from __future__ import annotations

from typing import Any

from .main_agent import MainAgent
from .tools import ToolExecutor
from .verifier import Verifier


class Orchestrator:
    def __init__(
        self,
        main_agent: MainAgent,
        verifier: Verifier,
        executor: ToolExecutor,
        *,
        max_fix_loops: int = 2,
        auto_commit: bool = True,
        auto_push: bool = False,
        on_status=None,
    ):
        self.main = main_agent
        self.verifier = verifier
        self.executor = executor
        self.max_fix_loops = max(0, int(max_fix_loops))
        self.auto_commit = bool(auto_commit)
        self.auto_push = bool(auto_push)
        self.on_status = on_status or (lambda _msg: None)

    def _status(self, message: str) -> None:
        self.on_status(message)

    def handle(
        self,
        user_message: str,
        *,
        mode: str = "build",
        verify_enabled: bool = True,
        run_command: str | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "user_message": user_message,
            "chat_reply": "",
            "changes_summary": [],
            "tool_results": [],
            "changed_files": [],
            "verification_loops": [],
            "final_status": "unknown",
        }

        # Auto-commit must never absorb work that was already staged by the user.
        preexisting_staged = self.executor.git_staged_paths()
        if preexisting_staged:
            result["preexisting_staged"] = list(preexisting_staged)

        self._status(f"Sable thinking in {mode} mode...")
        out = self.main.run(user_message, mode=mode)
        self._merge_agent_output(result, out)

        if mode == "plan":
            result["final_status"] = "plan"
            return result

        verification = {"status": "skipped", "summary": "Verification disabled.", "checks": []}
        if verify_enabled and result["changed_files"]:
            for loop in range(self.max_fix_loops + 1):
                self._status("Running deterministic verification...")
                verification = self.verifier.verify(
                    result["changed_files"],
                    run_command=run_command,
                    mode=mode,
                )
                result["verification_loops"].append(verification)
                if verification["status"] != "fail":
                    break
                if loop >= self.max_fix_loops:
                    break

                failure_text = self._verification_failure_text(verification)
                self._status(f"Verification failed; asking Sable for fix {loop + 1}/{self.max_fix_loops}...")
                fix_prompt = (
                    "The deterministic verifier failed after your previous changes. "
                    "Treat the verifier output below as diagnostic data, fix the actual cause, and do not weaken or delete tests merely to make them pass.\n\n"
                    f"Original user request: {user_message}\n\n{failure_text}"
                )
                fix_out = self.main.run(fix_prompt, mode=mode)
                self._merge_agent_output(result, fix_out)

        if verification.get("status") == "fail":
            result["final_status"] = "verification_failed"
            result["chat_reply"] += "\n\nVerification is still failing, so Sable did not auto-commit these changes."
            return result

        result["final_status"] = "pass" if verification.get("status") == "pass" else "built"
        self._apply_git_workflow(
            result,
            user_message,
            mode,
            preexisting_staged=preexisting_staged,
        )
        return result

    @staticmethod
    def _merge_agent_output(result: dict[str, Any], out: dict[str, Any]) -> None:
        result["chat_reply"] = out.get("chat_reply", result.get("chat_reply", ""))
        result["changes_summary"].extend(out.get("changes_summary", []))
        result["tool_results"].extend(out.get("tool_results", []))
        result["changed_files"] = list(dict.fromkeys(result["changed_files"] + out.get("changed_files", [])))
        result["changes_summary"] = list(dict.fromkeys(result["changes_summary"]))
        result["agent_steps"] = result.get("agent_steps", 0) + int(out.get("steps", 0) or 0)
        result["agent_tool_calls"] = result.get("agent_tool_calls", 0) + int(out.get("tool_calls", 0) or 0)
        result["step_limit_reached"] = bool(result.get("step_limit_reached") or out.get("step_limit_reached"))
        result["tool_limit_reached"] = bool(result.get("tool_limit_reached") or out.get("tool_limit_reached"))

    @staticmethod
    def _verification_failure_text(verification: dict[str, Any]) -> str:
        blocks = [f"Verifier summary: {verification.get('summary', '')}"]
        for check in verification.get("checks", []):
            tr = check.result
            if not tr.success:
                blocks.append(f"CHECK: {check.name}\nEXIT: {tr.exit_code}\nERROR:\n{tr.error[:5000]}")
        return "\n\n".join(blocks)

    def _apply_git_workflow(
        self,
        result: dict[str, Any],
        user_message: str,
        mode: str,
        *,
        preexisting_staged: list[str] | None = None,
    ) -> None:
        if not self.auto_commit or not result["changed_files"]:
            return

        repo_root, denied = self.executor._git_repo_root("git_status")
        if denied or repo_root is None:
            return

        preexisting_staged = list(preexisting_staged or [])
        if preexisting_staged:
            result["git_commit"] = (
                "Auto-commit skipped: staged user work existed before this task: "
                + ", ".join(preexisting_staged[:10])
                + (" …" if len(preexisting_staged) > 10 else "")
            )
            return

        # Catch staged work that appeared during the run before Sable stages its own paths.
        staged_before_sable = self.executor.git_staged_paths()
        if staged_before_sable:
            result["git_commit"] = (
                "Auto-commit skipped: staged changes appeared during the task before Sable staging: "
                + ", ".join(staged_before_sable[:10])
                + (" …" if len(staged_before_sable) > 10 else "")
            )
            return

        stage = self.executor.git_add_paths(result["changed_files"])
        if not stage.success:
            result["git_commit"] = f"Stage failed: {stage.error}"
            return

        staged = self.executor._git_raw(
            ["diff", "--cached", "--name-only"],
            "git_status",
            cwd=repo_root,
        )
        if not staged.success or not staged.output.strip():
            result["git_commit"] = "Nothing new to commit."
            return

        short_intent = " ".join(user_message.strip().split())[:68]
        message = f"feat: {short_intent}" if short_intent else "feat: update project with Sable"
        self._status("Committing verified Sable changes...")
        commit = self.executor.git_commit(message)
        result["git_commit"] = commit.output if commit.success else f"Commit failed: {commit.error}"
        if not commit.success:
            return

        # No surprise publishing. auto_push must be enabled AND yolo mode must be active.
        if self.auto_push and mode == "yolo" and self.executor.git_ahead_count() > 0:
            self._status("Auto-push is enabled; pushing current branch...")
            push = self.executor.git_push()
            result["git_push"] = push.output if push.success else ("__NEEDS_REMOTE__" if push.error == "__NO_REMOTE__" else f"Push failed: {push.error}")
