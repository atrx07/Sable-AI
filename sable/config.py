"""Configuration, secret redaction, and backwards-compatible config loading."""

from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path

CONFIG_DIR = Path(os.path.expanduser("~/.sable"))
CONFIG_FILE = CONFIG_DIR / "config.json"
LEGACY_GIT_CREDS_FILE = CONFIG_DIR / "git_creds.json"

DEFAULTS = {
    "groq_key_1": "",
    "groq_key_2": "",
    "groq_key_3": "",
    "active_key_index": 1,
    "token_usage": {"1": 0, "2": 0, "3": 0},
    "token_reset_date": "",
    "rate_limits": {},
    "main_model": "openai/gpt-oss-120b",
    "fast_model": "llama-3.1-8b-instant",
    "max_agent_steps": 12,
    "max_fix_loops": 2,
    "project_dir": os.path.expanduser("~/sable-projects"),
    "git_auto_commit": True,
    "git_auto_push": False,
    "verify_after_changes": True,
    "temperature": 0.2,
    "mode": "build",
    "command_timeout": 120,
}

# A short offline fallback list. `/models` fetches the live catalogue from Groq.
PRODUCTION_MODEL_HINTS = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
]

# Kept as a compatibility alias for older CLI imports.
AVAILABLE_MODELS = PRODUCTION_MODEL_HINTS

SECRET_BASENAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    "secrets.json",
    "credentials.json",
    "id_rsa",
    "id_ed25519",
    ".netrc",
    ".npmrc",
    ".pypirc",
}

SECRET_PATTERNS = [
    re.compile(r"gsk_[A-Za-z0-9]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{40,}"),
    re.compile(r"glpat-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{32,}"),
    re.compile(
        r"(?i)(api[_\-]?key|token|secret|password)\s*[:=]\s*[\"']?"
        r"[A-Za-z0-9_\-.]{16,}"
    ),
]


def is_blocked_path(path: str) -> bool:
    """Return True for paths Sable must never expose to the model."""
    raw = str(path).replace("\\", "/")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    lowered = [p.lower() for p in parts]

    if ".sable" in lowered:
        return True
    if ".ssh" in lowered:
        return True
    if any(p.startswith(".env") for p in lowered):
        return True
    if lowered and lowered[-1] in {p.lower() for p in SECRET_BASENAMES}:
        return True
    if lowered[-2:] == [".git", "credentials"]:
        return True
    return False


def redact_secrets(text: str) -> str:
    text = str(text)
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def contains_secret(text: str) -> bool:
    return any(pattern.search(str(text)) for pattern in SECRET_PATTERNS)


def _fresh_defaults() -> dict:
    cfg = dict(DEFAULTS)
    cfg["token_usage"] = dict(DEFAULTS["token_usage"])
    cfg["rate_limits"] = {}
    return cfg


def reset_daily_tokens(cfg: dict) -> bool:
    today = date.today().isoformat()
    if cfg.get("token_reset_date") == today:
        return False
    cfg["token_usage"] = {"1": 0, "2": 0, "3": 0}
    cfg["token_reset_date"] = today
    save_config(cfg)
    return True


def load_config() -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    cfg = _fresh_defaults()
    saved: dict = {}
    if CONFIG_FILE.exists():
        try:
            loaded = json.loads(CONFIG_FILE.read_text())
            if isinstance(loaded, dict):
                saved = loaded
                cfg.update(saved)
        except (OSError, json.JSONDecodeError):
            pass

    # Migrate old v1 names without preserving obsolete debug-agent behaviour.
    if "debug_model" in saved and "fast_model" not in saved:
        cfg["fast_model"] = saved.get("debug_model") or DEFAULTS["fast_model"]
    cfg.pop("debug_model", None)

    cfg.setdefault("token_usage", {})
    for key in ("1", "2", "3"):
        cfg["token_usage"].setdefault(key, 0)
    cfg.setdefault("rate_limits", {})

    env_key = os.environ.get("GROQ_API_KEY", "")
    if env_key and not cfg.get("groq_key_1"):
        cfg["groq_key_1"] = env_key

    reset_daily_tokens(cfg)
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2) + "\n")
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except OSError:
        pass


def configured_key_indices(cfg: dict) -> list[int]:
    return [i for i in (1, 2, 3) if cfg.get(f"groq_key_{i}", "")]


def get_active_key(cfg: dict) -> tuple[str, int]:
    configured = configured_key_indices(cfg)
    if not configured:
        return "", 1
    active = int(cfg.get("active_key_index", 1))
    if active in configured:
        return cfg[f"groq_key_{active}"], active
    first = configured[0]
    cfg["active_key_index"] = first
    return cfg[f"groq_key_{first}"], first


def rotate_to_next_key(cfg: dict, current: int | None = None) -> int:
    configured = configured_key_indices(cfg)
    if not configured:
        return 1
    current = int(current or cfg.get("active_key_index", configured[0]))
    if current not in configured:
        next_idx = configured[0]
    else:
        pos = configured.index(current)
        next_idx = configured[(pos + 1) % len(configured)]
    cfg["active_key_index"] = next_idx
    save_config(cfg)
    return next_idx
