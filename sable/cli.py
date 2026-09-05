"""Interactive Termux-friendly CLI for Sable v2."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .cli_settings import SettingsCommandsMixin
from .cli_workspace import WorkspaceCommandsMixin
from .config import LEGACY_GIT_CREDS_FILE, get_active_key, load_config
from .groq_client import GroqClient
from .main_agent import MainAgent
from .orchestrator import Orchestrator
from .providers import ModelRouter
from .security import VALID_MODES
from .tools import ToolExecutor
from .ui import ACCENT, B, BANNER, BLU, CYN, DIM, GRN, MGT, RED, R, YLW, HELP_TEXT, _hr, _mask
from .verifier import Verifier


class CLI(SettingsCommandsMixin, WorkspaceCommandsMixin):
    def __init__(self):
        self.cfg = load_config()
        self.mode = self.cfg.get("mode", "build") if self.cfg.get("mode") in VALID_MODES else "build"
        self.verify_enabled = bool(self.cfg.get("verify_after_changes", True))
        self.run_command: str | None = None
        self.current_project = "default"
        self.executor: ToolExecutor | None = None
        self.orchestrator: Orchestrator | None = None
        self._setup_project(self.current_project)

    def _setup_project(self, name: str) -> None:
        root = Path(self.cfg["project_dir"]).expanduser() / name
        root.mkdir(parents=True, exist_ok=True)
        self.executor = ToolExecutor(str(root), command_timeout=self.cfg.get("command_timeout", 120))
        self.current_project = name
        self._rebuild_agents()

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
        verifier = Verifier(self.executor)
        self.orchestrator = Orchestrator(
            agent,
            verifier,
            self.executor,
            max_fix_loops=self.cfg.get("max_fix_loops", 2),
            auto_commit=self.cfg.get("git_auto_commit", True),
            auto_push=self.cfg.get("git_auto_push", False),
            on_status=lambda msg: print(f"  {DIM}{msg}{R}"),
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
                status = verification.get("status", "unknown")
                color = GRN if status == "pass" else (RED if status == "fail" else DIM)
                print(f"  {color}{B}{status.upper()}{R}  {verification.get('summary', '')}")
                for check in verification.get("checks", []):
                    tr = check.result
                    icon = f"{GRN}✓{R}" if tr.success else f"{RED}✗{R}"
                    print(f"    {icon} {check.name}" + (f" ({tr.duration_ms}ms)" if tr.duration_ms else ""))
                    if not tr.success and tr.error:
                        print(f"      {RED}{tr.error.splitlines()[0][:100]}{R}")

        status = result.get("final_status")
        labels = {
            "pass": f"{GRN}{B}✅ Verified{R}",
            "built": f"{BLU}{B}🔨 Done{R}",
            "plan": f"{CYN}{B}📋 Plan only — no writes allowed{R}",
            "verification_failed": f"{RED}{B}❌ Verification still failing{R}",
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
        print(BANNER)
        key, idx = get_active_key(self.cfg)
        print(f"  Project : {B}{ACCENT}{self.current_project}{R}")
        print(f"  Model   : {B}{self.cfg['main_model']}{R}")
        print(f"  Key     : {B}{idx}{R} {_mask(key)}")
        print(f"  Mode    : {B}{self.mode}{R}")
        print(f"  Type {CYN}/help{R} for commands.\n")
        if LEGACY_GIT_CREDS_FILE.exists():
            print(f"  {YLW}⚠ Legacy ~/.sable/git_creds.json exists. Sable v2 ignores it; remove it after confirming your normal Git auth works.{R}\n")
        print(self._status_bar())

        while True:
            try:
                location = self._prompt_location()
                sys.stdout.write(
                    f"\n{MGT}{B}[{self.current_project}]{R} "
                    f"{BLU}{location}{R} {ACCENT}▶{R} "
                )
                sys.stdout.flush()
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
                )
                self._print_result(result)
            except Exception as exc:
                print(f"\n{RED}{B}Error:{R} {exc}\n")


def main() -> None:
    CLI().run()
