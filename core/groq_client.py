# core/groq_client.py
"""
Thin wrapper around Groq REST API with multi-key rotation + token tracking.
Guarantees all message content fields are strings before sending.
"""

import json
import re
import requests
from typing import Optional
from .config import rotate_to_next_key, save_config, redact_secrets

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"


def _sanitize_messages(messages: list) -> list:
    """
    Ensure every message has role (str) and content (str).
    Converts dicts/lists/None to JSON strings so the API never crashes.
    """
    clean = []
    for msg in messages:
        role = str(msg.get("role", "user"))
        content = msg.get("content", "")
        if content is None:
            content = ""
        elif isinstance(content, (dict, list)):
            try:
                content = json.dumps(content)
            except Exception:
                content = str(content)
        else:
            content = str(content)
        # Redact any secrets that may have crept into history
        content = redact_secrets(content)
        clean.append({"role": role, "content": content})
    return clean


class GroqClient:
    def __init__(self, cfg: dict, model: str, temperature: float = 0.3):
        self.cfg = cfg
        self.model = model
        self.temperature = temperature

    def _current_key(self):
        from .config import get_active_key
        key, idx = get_active_key(self.cfg)
        return key, idx

    def chat(self, messages: list, max_tokens: int = 4096) -> str:
        key, idx = self._current_key()
        if not key:
            raise RuntimeError("No Groq API key configured. Use /keys to add one.")

        # Sanitize ALL messages — guarantees content is always a string
        clean_messages = _sanitize_messages(messages)

        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": clean_messages,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
        }

        try:
            resp = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=120)
        except requests.RequestException as e:
            raise RuntimeError(f"Network error: {e}")

        # Rate-limited: rotate key and retry once
        if resp.status_code == 429:
            new_idx = rotate_to_next_key(self.cfg)
            if new_idx != idx:
                key2, _ = self._current_key()
                headers["Authorization"] = f"Bearer {key2}"
                resp = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=120)
            if resp.status_code == 429:
                raise RuntimeError("All Groq API keys are rate-limited. Wait a moment.")

        if resp.status_code != 200:
            raise RuntimeError(f"Groq API error {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        content = data["choices"][0]["message"]["content"]

        # Track token usage
        usage = data.get("usage", {})
        total_tokens = usage.get("total_tokens", 0)
        key_str = str(idx)
        self.cfg["token_usage"][key_str] = self.cfg["token_usage"].get(key_str, 0) + total_tokens
        save_config(self.cfg)

        return content

    def extract_json(self, text: str) -> Optional[dict]:
        """Pull first JSON object from response, trying code fences first."""
        # Try ```json ... ``` block
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except Exception:
                pass
        # Try bare JSON object
        match = re.search(r"(\{.*\})", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except Exception:
                pass
        return None
