"""CLI settings, Groq-key, model and mode commands."""

from __future__ import annotations

from getpass import getpass

from .config import PRODUCTION_MODEL_HINTS, get_active_key, load_config, save_config
from .groq_client import GroqClient
from .security import VALID_MODES
from .ui import ACCENT, B, CYN, DIM, GRN, RED, R, YLW, _hr, _mask


class SettingsCommandsMixin:
    def _cmd_keys(self, arg: str) -> None:
        parts = arg.split()
        if parts and parts[0].lower() == "use":
            try:
                idx = int(parts[1])
                if idx not in (1, 2, 3):
                    raise ValueError
            except (IndexError, ValueError):
                print(f"  {RED}Usage: /keys use <1|2|3>{R}")
                return
            if not self.cfg.get(f"groq_key_{idx}"):
                print(f"  {RED}Key {idx} is empty.{R}")
                return
            self.cfg["active_key_index"] = idx
            save_config(self.cfg)
            self._rebuild_agents()
            print(f"  {GRN}Preferred Groq key: {idx}{R}")
            return

        print(f"\n{B}Groq keys{R} {DIM}(input hidden; blank keeps current value){R}")
        for i in (1, 2, 3):
            current = self.cfg.get(f"groq_key_{i}", "")
            print(f"  {i}: {_mask(current)}")
        for i in (1, 2, 3):
            value = getpass(f"  Key {i}: ").strip()
            if value:
                self.cfg[f"groq_key_{i}"] = value
        configured = [i for i in (1, 2, 3) if self.cfg.get(f"groq_key_{i}")]
        if configured and self.cfg.get("active_key_index") not in configured:
            self.cfg["active_key_index"] = configured[0]
        save_config(self.cfg)
        self._rebuild_agents()

    def _cmd_models(self) -> None:
        if not self._ensure_key():
            return
        assert self.executor is not None
        client = GroqClient(self.cfg, self.cfg["main_model"], self.cfg.get("temperature", 0.2))
        try:
            models = client.list_models()
        except Exception as exc:
            print(f"  {RED}{exc}{R}")
            print(f"  {DIM}Offline production hints: {', '.join(PRODUCTION_MODEL_HINTS)}{R}")
            return
        print(f"\n{B}Groq models ({len(models)}):{R}")
        for model in models:
            marker = f"{ACCENT}●{R}" if model == self.cfg["main_model"] else f"{DIM}•{R}"
            print(f"  {marker} {model}")
        print(f"\n  Change model with {CYN}/config{R}.")

    def _cmd_config(self) -> None:
        print(f"\n{B}Sable config{R}")
        keys = (
            "main_model", "fast_model", "max_agent_steps", "max_tool_calls", "max_fix_loops", "temperature",
            "git_auto_commit", "git_auto_push", "verify_after_changes", "command_timeout", "project_dir",
        )
        for key in keys:
            print(f"  {key}: {self.cfg.get(key)}")
        print(f"  mode: {self.mode}")
        print(f"\n{DIM}To change the main model, enter its Groq model ID; blank keeps current.{R}")
        model = input("  main_model: ").strip()
        if model:
            self.cfg["main_model"] = model
            save_config(self.cfg)
            self._rebuild_agents()
            print(f"  {GRN}Model updated.{R}")

    def _cmd_mode(self, arg: str) -> None:
        mode = arg.strip().lower()
        if not mode:
            print(f"  Mode: {self.mode}")
            return
        if mode not in VALID_MODES:
            print(f"  {RED}Usage: /mode plan|build|yolo{R}")
            return
        self.mode = mode
        self.cfg["mode"] = mode
        save_config(self.cfg)
        warning = f" {RED}High-risk local actions are now permitted.{R}" if mode == "yolo" else ""
        print(f"  {GRN}Mode set to {mode}.{R}{warning}")

    def _cmd_verify(self, arg: str) -> None:
        value = arg.strip().lower()
        if value in {"on", "1", "true"}:
            self.verify_enabled = True
        elif value in {"off", "0", "false"}:
            self.verify_enabled = False
        elif value:
            print(f"  {RED}Usage: /verify on|off{R}")
            return
        self.cfg["verify_after_changes"] = self.verify_enabled
        save_config(self.cfg)
        print(f"  Verification: {'ON' if self.verify_enabled else 'OFF'}")
