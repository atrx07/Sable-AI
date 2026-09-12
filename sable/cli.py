"""Interactive Termux-friendly CLI for Sable v2."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .capabilities import ApprovalDecision, CapabilityRequest
from .cli_args import resolve_workspace
from .cli_settings import SettingsCommandsMixin
from .cli_workspace import WorkspaceCommandsMixin
from .config import LEGACY_GIT_CREDS_FILE, get_active_key, load_config
from .groq_client import GroqClient
from .main_agent import MainAgent
from .orchestrator import Orchestrator
from .presentation import PlainRenderer, create_renderer
from .providers import ModelRouter
from .security import VALID_MODES
from .sessions import SessionManager
from .tools import ToolExecutor
from .ui import ACCENT, B, BANNER, BLU, CYN, DIM, GRN, MGT, RED, R, YLW, HELP_TEXT, _hr, _mask
from .verifier import Verifier
from .verification import VerificationScope


class CLI(SettingsCommandsMixin, WorkspaceCommandsMixin):
    def __init__(
        self,
        workspace: str | Path | None = None,
        *,
        interactive_approvals: bool = True,
    ):
        self.cfg = load_config()
        self.mode = self.cfg.get("mode", "build") if self.cfg.get("mode") in VALID_MODES else "build"
        self.verify_enabled = bool(self.cfg.get("verify_after_changes", True))
        try:
            self.verification_scope = VerificationScope.parse(
                self.cfg.get("verification_scope", "affected")
            ).value.lower()
        except ValueError:
            self.verification_scope = "affected"
        self.run_command: str | None = None
        self.current_project = "default"
        self.executor: ToolExecutor | None = None
        self.orchestrator: Orchestrator | None = None
        self.sessions: SessionManager | None = None
        self.session_error: str | None = None
        self.direct_workspace = workspace is not None
        self.interactive_approvals = bool(interactive_approvals)
        self.plain = False
        self.no_color = False
        self.quiet = False
        self.verbose = False
        self.renderer: PlainRenderer = create_renderer()
        if workspace is None:
            self._setup_project(self.current_project)
        else:
            self._setup_workspace(workspace)

    def _setup_project(self, name: str) -> None:
        root = Path(self.cfg["project_dir"]).expanduser() / name
        root.mkdir(parents=True, exist_ok=True)
        self.direct_workspace = False
        self._bind_workspace(root, name)

    def _setup_workspace(self, workspace: str | Path) -> None:
        root = resolve_workspace(str(workspace))
        self.direct_workspace = True
        self._bind_workspace(root, root.name or str(root))

    def _bind_workspace(self, root: Path, label: str) -> None:
        self.executor = ToolExecutor(
            str(root),
            command_timeout=self.cfg.get("command_timeout", 120),
            execution_backend=self.cfg.get("execution_backend", "auto"),
            proot_rootfs=self.cfg.get("proot_rootfs") or None,
        )
        self.current_project = label
        try:
            self.sessions = SessionManager(
                root,
                provider="groq",
                main_model=self.cfg.get("main_model", "unknown"),
                fast_model=self.cfg.get("fast_model", "unknown"),
            )
            self.session_error = None
        except Exception as exc:
            # Persistence is an observability enhancement, never a reason to
            # prevent the coding runtime from starting.
            self.sessions = None
            self.session_error = str(exc)
        session_id = self.sessions.current.session_id if self.sessions and self.sessions.current else None
        self.executor.configure_approvals(
            session_id=session_id,
            handler=self._approval_prompt,
        )
        self._rebuild_agents()

    def _approval_prompt(self, request: CapabilityRequest) -> ApprovalDecision:
        if not self.interactive_approvals:
            return ApprovalDecision.DENY
        return self.renderer.prompt_approval(request)

    def configure_presentation(
        self,
        *,
        plain: bool = False,
        no_color: bool = False,
        quiet: bool = False,
        verbose: bool = False,
        stdout=None,
        stderr=None,
    ) -> None:
        self.plain = bool(plain)
        self.no_color = bool(no_color)
        self.quiet = bool(quiet)
        self.verbose = bool(verbose)
        self.renderer = create_renderer(
            stream=stdout,
            error_stream=stderr,
            plain=self.plain,
            no_color=self.no_color,
            quiet=self.quiet,
            verbose=self.verbose,
        )
        if self.orchestrator is not None:
            self.orchestrator.on_status = self.renderer.status
            self.orchestrator.on_event = self.renderer.on_event

    def run_once(self, user_message: str) -> dict:
        """Run one task without entering the interactive shell."""
        if not str(user_message).strip():
            return {
                "final_status": "configuration_error",
                "chat_reply": "A non-empty task is required.",
                "changed_files": [],
                "verification_loops": [],
            }
        key, _ = get_active_key(self.cfg)
        if not key:
            return {
                "final_status": "configuration_error",
                "chat_reply": "No Groq API key configured. Run `sable` and use /keys.",
                "changed_files": [],
                "verification_loops": [],
            }
        if self.orchestrator is None:
            self._rebuild_agents()
        if self.orchestrator is None:
            return {
                "final_status": "provider_error",
                "chat_reply": "The configured provider could not be initialized.",
                "changed_files": [],
                "verification_loops": [],
            }
        return self.orchestrator.handle(
            user_message,
            mode=self.mode,
            verify_enabled=self.verify_enabled,
            run_command=self.run_command,
            verification_scope=self.verification_scope,
        )

    def _rebuild_agents(self) -> None:
        key, _ = get_active_key(self.cfg)
        if not key or self.executor is None:
            self.orchestrator = None
            return
        client = GroqClient(self.cfg, self.cfg["main_model"], self.cfg.get("temperature", 0.2))
        fast_client = GroqClient(self.cfg, self.cfg["fast_model"], self.cfg.get("temperature", 0.2))
        router = ModelRouter(client, fast_client)
        agent = MainAgent(
            client,
            self.executor,
            max_steps=self.cfg.get("max_agent_steps", 12),
            max_tool_calls=self.cfg.get("max_tool_calls", 24),
            router=router,
        )
        verifier = Verifier(self.executor, default_scope=self.verification_scope)
        self.orchestrator = Orchestrator(
            agent,
            verifier,
            self.executor,
            max_fix_loops=self.cfg.get("max_fix_loops", 2),
            auto_commit=self.cfg.get("git_auto_commit", True),
            auto_push=self.cfg.get("git_auto_push", False),
            verification_scope=self.verification_scope,
            session_manager=self.sessions,
            on_status=self.renderer.status,
            on_event=self.renderer.on_event,
        )

    def _ensure_key(self) -> bool:
        key, _ = get_active_key(self.cfg)
        if key:
            return True
        print(f"\n{YLW}No Groq API key configured yet.{R}")
        self._cmd_keys("")
        key, _ = get_active_key(self.cfg)
        if key:
            self._rebuild_agents()
        return bool(key)

    def _status_bar(self) -> str:
        self.cfg = load_config()
        key, idx = get_active_key(self.cfg)
        tokens = self.cfg.get("token_usage", {}).get(str(idx), 0)
        slots = []
        for i in (1, 2, 3):
            present = bool(self.cfg.get(f"groq_key_{i}"))
            if i == idx and present:
                slots.append(f"{ACCENT}{B}●{R}")
            elif present:
                slots.append(f"{DIM}●{R}")
            else:
                slots.append(f"{DIM}○{R}")
        cwd = self.executor.current_dir if self.executor else "?"
        home = os.path.expanduser("~")
        if cwd.startswith(home):
            cwd = "~" + cwd[len(home):]
        mode_color = {"plan": CYN, "build": GRN, "yolo": RED}[self.mode]
        undo = ""
        if self.executor and self.executor.transactions.last is not None:
            undo = f" {DIM}│{R} {YLW}↶ undo{R}"
        return (
            f"  {DIM}┤{R} {mode_color}{B}{self.mode}{R} {DIM}│{R} "
            f"Key {idx} {' '.join(slots)} {DIM}│{R} Tokens {tokens:,} {DIM}│{R} {BLU}{cwd}{R}"
            f"{undo} {DIM}├{R}"
        )

    def _prompt_location(self) -> str:
        if self.executor is None:
            return "?"
        try:
            rel = self.executor.workspace.relative(self.executor.workspace.cwd)
        except (OSError, ValueError):
            return "?"
        return "~" if rel == "." else f"~/{rel}"

    def _print_result(self, result: dict) -> None:
        self.renderer.render_result(result, verification_enabled=self.verify_enabled)

    def _print_result_legacy(self, result: dict) -> None:
        print()
        print(_hr("═", color=ACCENT))
        print(f"{B}{ACCENT}  Sable Reply{R}")
        print(_hr())
        for line in result.get("chat_reply", "").splitlines() or [""]:
            print(f"  {line}")

        if result.get("changes_summary"):
            print(f"\n{B}{GRN}  Changes:{R}")
            for item in result["changes_summary"]:
                print(f"  {GRN}▸{R} {item}")

        tools = result.get("tool_results", [])
        if tools:
            print(f"\n{B}{BLU}  Tool loop:{R}")
            for tr in tools:
                icon = f"{GRN}✓{R}" if tr.success else (f"{YLW}!{R}" if tr.approval_required else f"{RED}✗{R}")
                detail = (tr.output if tr.success else tr.error).splitlines()[0] if (tr.output or tr.error) else ""
                print(f"  {icon} {DIM}{tr.tool}{R}" + (f"  → {detail[:90]}" if detail else ""))

        loops = result.get("verification_loops", [])
        if loops:
            print(f"\n{B}{YLW}  Verification:{R}")
            for idx, verification in enumerate(loops, 1):
                status = verification.get("overall_status", verification.get("status", "unknown"))
                normalized = str(status).upper()
                color = GRN if normalized.startswith("PASS") else (RED if normalized == "FAIL" else YLW)
                scope = verification.get("scope", "")
                stage = verification.get("stage", f"run-{idx}")
                context = " / ".join(item for item in (stage, str(scope).lower()) if item)
                print(f"  {color}{B}{normalized}{R}" + (f" {DIM}({context}){R}" if context else "") + f"  {verification.get('summary', '')}")
                for check in verification.get("checks", []):
                    if isinstance(check, dict):
                        check_meta = check.get("check", {})
                        tr = check.get("result", {})
                        check_status = str(check.get("status", "unknown"))
                        name = check_meta.get("name", "check")
                        duration = tr.get("duration_ms", 0)
                        detail = check.get("diagnostic") or tr.get("error", "")
                        classification = check.get("classification", "")
                    else:
                        tr = check.result
                        status_value = getattr(check, "status", "unknown")
                        check_status = str(getattr(status_value, "value", status_value))
                        name = check.name
                        duration = tr.duration_ms
                        detail = getattr(check, "diagnostic", "") or tr.error
                        classification_value = getattr(check, "classification", "")
                        classification = str(getattr(classification_value, "value", classification_value))
                    icon = f"{GRN}✓{R}" if check_status == "PASS" else (f"{YLW}○{R}" if check_status.startswith("SKIPPED") else f"{RED}✗{R}")
                    suffix = f" [{classification}]" if classification and classification != "NONE" else ""
                    print(f"    {icon} {name} — {check_status}{suffix}" + (f" ({duration}ms)" if duration else ""))
                    if detail and check_status != "PASS":
                        print(f"      {RED}{str(detail).splitlines()[0][:100]}{R}")
                for warning in verification.get("integrity_warnings", []):
                    print(f"    {YLW}! integrity: {str(warning)[:120]}{R}")

        status = result.get("final_status")
        labels = {
            "pass": f"{GRN}{B}✅ Verified{R}",
            "built": f"{BLU}{B}🔨 Done{R}",
            "plan": f"{CYN}{B}📋 Plan only — no writes allowed{R}",
            "verification_failed": f"{RED}{B}❌ Verification still failing{R}",
            "verification_incomplete": f"{YLW}{B}⚠ Verification incomplete — no commit created{R}",
            "verification_blocked": f"{YLW}{B}⛔ Verification blocked by policy{R}",
            "verification_integrity_blocked": f"{RED}{B}⛔ Verification integrity check blocked commit{R}",
            "repair_no_progress": f"{RED}{B}⛔ Repair stopped after making no progress{R}",
            "blocked": f"{YLW}{B}⛔ Task stopped by a runtime limit or policy{R}",
            "aborted": f"{RED}{B}⛔ Task aborted — recovery attempted{R}",
        }
        if status in labels:
            print(f"\n  {labels[status]}")

        if result.get("git_commit"):
            print(f"  {MGT}📦 {result['git_commit']}{R}")
        if result.get("git_push") == "__NEEDS_REMOTE__":
            print(f"  {YLW}⚠ No origin remote configured.{R}")
        elif result.get("git_push"):
            print(f"  {MGT}📤 {result['git_push']}{R}")

        if result.get("undo_available") and result.get("changed_files"):
            snapshots = result.get("transaction_snapshot_count", 0)
            print(
                f"  {YLW}↶ Reversible file checkpoint available with /undo"
                + (f" ({snapshots} snapshot{'s' if snapshots != 1 else ''})" if snapshots else "")
                + f".{R}"
            )

        print(_hr("═", color=ACCENT))
        print(self._status_bar())

    def run(self) -> None:
        backend = self.executor.execution_backend_status() if self.executor else {}
        self.renderer.render_startup({
            "workspace": self.executor.current_dir if self.executor else self.current_project,
            "provider": "Groq",
            "model": self.cfg.get("main_model", "unknown"),
            "backend": backend.get("name", "unknown"),
            "mode": self.mode,
            "verification": self.verification_scope if self.verify_enabled else "off",
        })
        self.renderer.message("Type /help for commands.")
        if LEGACY_GIT_CREDS_FILE.exists():
            self.renderer.status(
                "Legacy ~/.sable/git_creds.json exists. Sable v2 ignores it; "
                "remove it after confirming your normal Git auth works."
            )
        if self.renderer.color_enabled:
            print(self._status_bar(), file=self.renderer.stream)

        while True:
            try:
                location = self._prompt_location()
                if self.renderer.color_enabled:
                    prompt = (
                        f"\n{MGT}{B}[{self.current_project}]{R} "
                        f"{BLU}{location}{R} {ACCENT}▶{R} "
                    )
                else:
                    prompt = f"\n[{self.current_project}] {location} · {self.mode} > "
                self.renderer.stream.write(prompt)
                self.renderer.stream.flush()
                raw = sys.stdin.readline()
                if raw == "":
                    raise EOFError
                user_input = raw.strip()
            except (EOFError, KeyboardInterrupt):
                print(f"\n{DIM}Bye!{R}")
                return
            if not user_input:
                continue

            if user_input.startswith("/"):
                parts = user_input[1:].split(None, 1)
                cmd = parts[0].lower()
                arg = parts[1] if len(parts) > 1 else ""
                if cmd in {"exit", "quit", "q"}:
                    print(f"{DIM}Bye!{R}")
                    return
                if cmd == "help":
                    print(HELP_TEXT)
                elif cmd == "keys":
                    self._cmd_keys(arg)
                elif cmd == "models":
                    self._cmd_models()
                elif cmd == "config":
                    self._cmd_config()
                elif cmd == "mode":
                    self._cmd_mode(arg)
                elif cmd in {"verify", "debug"}:  # /debug kept as a compatibility alias
                    self._cmd_verify(arg)
                elif cmd == "run":
                    self.run_command = arg.strip() or None
                    print(f"  Run override: {self.run_command or '(auto-detect)'}")
                elif cmd == "project":
                    self._cmd_project(arg)
                elif cmd == "projects":
                    self._cmd_projects()
                elif cmd == "git":
                    self._cmd_git(arg)
                elif cmd == "undo":
                    self._cmd_undo(arg)
                elif cmd in {"txn", "transaction"}:
                    self._cmd_transaction(arg)
                elif cmd in {"session", "sessions"}:
                    self._cmd_session(arg)
                elif cmd in {"trace", "traces"}:
                    self._cmd_trace(arg)
                elif cmd in {"sandbox", "execution"}:
                    self._cmd_sandbox(arg)
                elif cmd == "clear":
                    if self.orchestrator:
                        self.orchestrator.main.reset_history()
                    print(f"  {GRN}Conversation history cleared.{R}")
                elif cmd == "history":
                    history = self.orchestrator.main.history if self.orchestrator else []
                    for item in history[-10:]:
                        print(f"  {item['role']}: {item['content'][:120]}")
                elif not self._handle_file_command(cmd, arg):
                    print(f"  {RED}Unknown command /{cmd}. Type /help.{R}")
                continue

            if not self._ensure_key():
                print(f"  {RED}Cannot proceed without a Groq API key.{R}")
                continue
            if self.orchestrator is None:
                self._rebuild_agents()
            assert self.orchestrator is not None
            try:
                result = self.orchestrator.handle(
                    user_input,
                    mode=self.mode,
                    verify_enabled=self.verify_enabled,
                    run_command=self.run_command,
                    verification_scope=self.verification_scope,
                )
                self._print_result(result)
            except Exception as exc:
                print(f"\n{RED}{B}Error:{R} {exc}\n")


def main() -> None:
    # Retained for callers importing sable.cli:main directly. The installed
    # console entry point uses sable.cli_app:main for standard argument parsing.
    CLI().run()
