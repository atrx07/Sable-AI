"""CLI settings, provider credentials, model and mode commands."""

from __future__ import annotations

from getpass import getpass

from .config import (
    PROVIDER_KEY_FIELDS,
    PROVIDER_MODEL_HINTS,
    SUPPORTED_PROVIDERS,
    configured_key_indices,
    get_configured_key,
    get_provider,
    get_provider_key,
    save_config,
)
from .groq_client import GroqClient
from .presentation import PlainRenderer
from .security import VALID_MODES
from .ui import _mask


class SettingsCommandsMixin:
    def _settings_renderer(self):
        return getattr(self, "renderer", None) or PlainRenderer()

    def _cmd_keys(self, arg: str) -> None:
        provider = get_provider(self.cfg)
        if provider != "groq":
            if arg.strip():
                self._settings_renderer().message("Usage: /keys")
                return
            current = get_provider_key(self.cfg, provider)
            self._settings_renderer().message(
                f"\n{provider} key (input hidden; blank keeps current value)"
            )
            self._settings_renderer().message(f"  key: {_mask(current)}")
            value = getpass(f"  {provider} key: ").strip()
            if value:
                self.cfg[PROVIDER_KEY_FIELDS[provider]] = value
                save_config(self.cfg)
            self._rebuild_agents()
            return

        parts = arg.split()
        if parts and parts[0].lower() == "use":
            try:
                idx = int(parts[1])
                if idx not in (1, 2, 3):
                    raise ValueError
            except (IndexError, ValueError):
                self._settings_renderer().message("Usage: /keys use <1|2|3>")
                return
            if not get_configured_key(self.cfg, idx):
                self._settings_renderer().message(f"Key {idx} is empty.")
                return
            self.cfg["active_key_index"] = idx
            save_config(self.cfg)
            self._rebuild_agents()
            self._settings_renderer().message(f"Preferred Groq key: {idx}")
            return

        self._settings_renderer().message("\nGroq keys (input hidden; blank keeps current value)")
        for i in (1, 2, 3):
            current = get_configured_key(self.cfg, i)
            self._settings_renderer().message(f"  {i}: {_mask(current)}")
        for i in (1, 2, 3):
            value = getpass(f"  Key {i}: ").strip()
            if value:
                self.cfg[f"groq_key_{i}"] = value
        configured = configured_key_indices(self.cfg)
        if configured and self.cfg.get("active_key_index") not in configured:
            self.cfg["active_key_index"] = configured[0]
        save_config(self.cfg)
        self._rebuild_agents()

    def _cmd_models(self) -> None:
        if not self._ensure_key():
            return
        assert self.executor is not None
        provider = get_provider(self.cfg)
        client = GroqClient(self.cfg, self.cfg["main_model"], self.cfg.get("temperature", 0.2))
        try:
            models = client.list_models()
        except Exception as exc:
            self._settings_renderer().message(str(exc), error=True)
            hints = PROVIDER_MODEL_HINTS.get(provider, [])
            if hints:
                self._settings_renderer().message(f"Offline hints: {', '.join(hints)}")
            else:
                self._settings_renderer().message(
                    f"{provider} model IDs are discovered live; check the provider console/docs "
                    "if the catalogue cannot be fetched."
                )
            return
        self._settings_renderer().message(f"\n{provider} models ({len(models)}):")
        for model in models:
            marker = "*" if model == self.cfg["main_model"] else "-"
            self._settings_renderer().message(f"  {marker} {model}")
        self._settings_renderer().message("\nChange model with /config.")

    def _cmd_config(self) -> None:
        provider = get_provider(self.cfg)
        keys = (
            "provider",
            "main_model",
            "fast_model",
            "max_agent_steps",
            "max_tool_calls",
            "max_fix_loops",
            "temperature",
            "git_auto_commit",
            "git_auto_push",
            "verify_after_changes",
            "verification_scope",
            "command_timeout",
            "execution_backend",
            "proot_rootfs",
            "project_dir",
        )
        rows = [(key, self.cfg.get(key)) for key in keys]
        rows.append(("mode", self.mode))
        self._settings_renderer().render_fields("Sable config (effective values)", rows)
        self._settings_renderer().message(
            "CLI --mode/--verify overrides apply only to this process. Config file: ~/.sable/config.json"
        )
        self._settings_renderer().message(
            "Enter model IDs for the active provider; blank keeps current values."
        )

        changed = False

        # Keep the long-standing two model prompts first so scripted/test input
        # written for older Sable releases remains valid.
        main_model = self._readline("main_model: ").strip()
        fast_model = self._readline("fast_model: ").strip()
        if main_model and main_model != self.cfg.get("main_model"):
            self.cfg["main_model"] = main_model
            changed = True
        if fast_model and fast_model != self.cfg.get("fast_model"):
            self.cfg["fast_model"] = fast_model
            changed = True

        # Provider selection is an optional extension to the legacy /config flow.
        # EOF means an older scripted interaction supplied only the two model lines.
        try:
            requested_provider = (
                self._readline(f"provider [{'/'.join(SUPPORTED_PROVIDERS)}] ({provider}): ")
                .strip()
                .lower()
            )
        except EOFError:
            requested_provider = ""

        if requested_provider:
            if requested_provider not in SUPPORTED_PROVIDERS:
                self._settings_renderer().message(
                    f"Unknown provider '{requested_provider}'. Choose: "
                    f"{', '.join(SUPPORTED_PROVIDERS)}",
                    error=True,
                )
                return
            if requested_provider != provider:
                self.cfg["provider"] = requested_provider
                provider = requested_provider
                changed = True

        if changed:
            save_config(self.cfg)
            self._rebuild_agents()
            self._settings_renderer().message(
                f"Provider/model config updated: {provider}. Run /keys and /models if needed."
            )
        else:
            self._settings_renderer().message("Config unchanged.")

    def _cmd_mode(self, arg: str) -> None:
        mode = arg.strip().lower()
        if not mode:
            self._settings_renderer().message(f"Mode: {self.mode}")
            return
        if mode not in VALID_MODES:
            self._settings_renderer().message("Usage: /mode plan|build|yolo")
            return
        self.mode = mode
        self.cfg["mode"] = mode
        save_config(self.cfg)
        warning = (
            " High-risk local actions are now requestable through approval."
            if mode == "yolo"
            else ""
        )
        self._settings_renderer().message(f"Mode set to {mode}.{warning}")

    def _cmd_verify(self, arg: str) -> None:
        value = arg.strip().lower()
        if value.startswith("scope "):
            value = value.split(None, 1)[1].strip()
        if value in {"on", "1", "true"}:
            self.verify_enabled = True
        elif value in {"off", "0", "false"}:
            self.verify_enabled = False
        elif value in {"quick", "affected", "full"}:
            self.verification_scope = value
        elif value:
            self._settings_renderer().message(
                "Usage: /verify on|off|quick|affected|full or /verify scope <scope>"
            )
            return
        self.cfg["verify_after_changes"] = self.verify_enabled
        self.cfg["verification_scope"] = self.verification_scope
        save_config(self.cfg)
        self._settings_renderer().message(
            f"Verification: {'ON' if self.verify_enabled else 'OFF'} ({self.verification_scope})"
        )
