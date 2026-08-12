# core/config.py
import os
import json
import re
from datetime import date

CONFIG_DIR  = os.path.expanduser("~/.sable")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
GIT_CREDS_FILE = os.path.join(CONFIG_DIR, "git_creds.json")

DEFAULTS = {
    "groq_key_1": "",
    "groq_key_2": "",
    "groq_key_3": "",
    "active_key_index": 1,
    "token_usage": {"1": 0, "2": 0, "3": 0},
    "token_reset_date": "",          # ISO date string — resets daily to match Groq
    "main_model":  "llama-3.3-70b-versatile",
    "debug_model": "llama-3.1-8b-instant",
    "max_debug_loops": 3,
    "project_dir": os.path.expanduser("~/sable-projects"),
    "git_auto_commit": True,
    "temperature": 0.3,
}

AVAILABLE_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "llama3-70b-8192",
    "llama3-8b-8192",
    "mixtral-8x7b-32768",
    "gemma2-9b-it",
]

# Files the agent must never read
BLOCKED_PATHS = {
    ".env", ".env.local", ".env.production", ".env.development",
    "config.json", "secrets.json", "credentials.json",
    ".sable/config.json", ".sable/git_creds.json",
    ".ssh/id_rsa", ".ssh/id_ed25519", ".ssh/config",
    ".netrc", ".npmrc", ".pypirc",
}

# Regex patterns that flag secrets
SECRET_PATTERNS = [
    re.compile(r'gsk_[A-Za-z0-9]{40,}'),
    re.compile(r'ghp_[A-Za-z0-9]{36,}'),
    re.compile(r'github_pat_[A-Za-z0-9_]{80,}'),
    re.compile(r'glpat-[A-Za-z0-9\-_]{20,}'),
    re.compile(r'sk-[A-Za-z0-9]{40,}'),
    re.compile(r'(?i)(api[_\-]?key|token|secret|password)\s*[:=]\s*["\']?[A-Za-z0-9_\-\.]{16,}'),
]


def is_blocked_path(path: str) -> bool:
    norm = os.path.normpath(path).lstrip("/\\")
    basename = os.path.basename(norm)
    if norm in BLOCKED_PATHS or basename in BLOCKED_PATHS:
        return True
    if ".sable" in norm or norm.startswith("~/.sable"):
        return True
    return False


def redact_secrets(text: str) -> str:
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def contains_secret(text: str) -> bool:
    return any(p.search(text) for p in SECRET_PATTERNS)


def reset_daily_tokens(cfg: dict) -> bool:
    """
    Reset token counters if today is a new day (Groq resets daily).
    Returns True if a reset was performed.
    """
    today = date.today().isoformat()
    last = cfg.get("token_reset_date", "")
    if last != today:
        cfg["token_usage"] = {"1": 0, "2": 0, "3": 0}
        cfg["token_reset_date"] = today
        save_config(cfg)
        return True
    return False


def load_config() -> dict:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            saved = json.load(f)
        cfg = {**DEFAULTS, **saved}
        if "token_usage" not in cfg:
            cfg["token_usage"] = {"1": 0, "2": 0, "3": 0}
        for k in ("1", "2", "3"):
            cfg["token_usage"].setdefault(k, 0)
    else:
        cfg = dict(DEFAULTS)
        cfg["token_usage"] = {"1": 0, "2": 0, "3": 0}
    if os.environ.get("GROQ_API_KEY") and not cfg["groq_key_1"]:
        cfg["groq_key_1"] = os.environ["GROQ_API_KEY"]
    reset_daily_tokens(cfg)
    return cfg


def save_config(cfg: dict):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def get_active_key(cfg: dict):
    idx = cfg.get("active_key_index", 1)
    order = list(range(idx, 4)) + list(range(1, idx))
    for i in order:
        key = cfg.get(f"groq_key_{i}", "")
        if key:
            return key, i
    return "", 1


def rotate_to_next_key(cfg: dict) -> int:
    current = cfg.get("active_key_index", 1)
    for offset in range(1, 4):
        next_i = (current - 1 + offset) % 3 + 1
        if cfg.get(f"groq_key_{next_i}", ""):
            cfg["active_key_index"] = next_i
            save_config(cfg)
            return next_i
    return current


def load_git_creds() -> dict:
    if os.path.exists(GIT_CREDS_FILE):
        with open(GIT_CREDS_FILE, "r") as f:
            return json.load(f)
    return {"username": "", "token": "", "email": ""}


def save_git_creds(creds: dict):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(GIT_CREDS_FILE, "w") as f:
        json.dump(creds, f, indent=2)
    os.chmod(GIT_CREDS_FILE, 0o600)


def build_authenticated_remote(url: str, creds: dict) -> str:
    username = creds.get("username", "")
    token = creds.get("token", "")
    if not username or not token:
        return url
    if url.startswith("https://"):
        return url.replace("https://", f"https://{username}:{token}@")
    return url
