"""Groq Chat Completions client with native tool calling and key rotation."""

from __future__ import annotations

import json
from typing import Any

import requests

from .config import (
    configured_key_indices,
    get_active_key,
    redact_secrets,
    rotate_to_next_key,
    save_config,
)

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
CHAT_URL = f"{GROQ_BASE_URL}/chat/completions"
MODELS_URL = f"{GROQ_BASE_URL}/models"


class GroqClient:
    def __init__(self, cfg: dict, model: str, temperature: float = 0.2):
        self.cfg = cfg
        self.model = model
        self.temperature = float(temperature)

    def _request_order(self) -> list[int]:
        configured = configured_key_indices(self.cfg)
        if not configured:
            return []
        _, active = get_active_key(self.cfg)
        if active not in configured:
            return configured
        pos = configured.index(active)
        return configured[pos:] + configured[:pos]

    def _record_headers(self, idx: int, response: requests.Response) -> None:
        keys = (
            "x-ratelimit-limit-requests",
            "x-ratelimit-limit-tokens",
            "x-ratelimit-remaining-requests",
            "x-ratelimit-remaining-tokens",
            "x-ratelimit-reset-requests",
            "x-ratelimit-reset-tokens",
            "retry-after",
        )
        snapshot = {key: response.headers.get(key, "") for key in keys if response.headers.get(key) is not None}
        self.cfg.setdefault("rate_limits", {})[str(idx)] = snapshot

    def _record_usage(self, idx: int, data: dict) -> None:
        usage = data.get("usage", {}) if isinstance(data, dict) else {}
        total = int(usage.get("total_tokens", 0) or 0)
        self.cfg.setdefault("token_usage", {}).setdefault(str(idx), 0)
        self.cfg["token_usage"][str(idx)] += total
        self.cfg["active_key_index"] = idx
        save_config(self.cfg)

    def _post(self, payload: dict) -> dict:
        order = self._request_order()
        if not order:
            raise RuntimeError("No Groq API key configured. Use /keys to add one.")

        rate_limited: list[tuple[int, str]] = []
        auth_failed: list[int] = []

        for idx in order:
            key = self.cfg.get(f"groq_key_{idx}", "")
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            try:
                response = requests.post(CHAT_URL, headers=headers, json=payload, timeout=120)
            except requests.RequestException as exc:
                raise RuntimeError(f"Groq network error: {exc}") from exc

            self._record_headers(idx, response)

            if response.status_code == 429:
                rate_limited.append((idx, response.headers.get("retry-after", "")))
                continue
            if response.status_code in (401, 403):
                auth_failed.append(idx)
                continue
            if response.status_code != 200:
                body = redact_secrets(response.text[:500])
                raise RuntimeError(f"Groq API error {response.status_code}: {body}")

            data = response.json()
            self._record_usage(idx, data)
            return data

        # Rotate the configured active slot for the next request even when all failed.
        if order:
            rotate_to_next_key(self.cfg, order[-1])

        if len(auth_failed) == len(order):
            raise RuntimeError("All configured Groq keys were rejected. Check them with /keys.")
        if rate_limited:
            waits = [wait for _, wait in rate_limited if wait]
            suffix = f" Retry after about {waits[0]}s." if waits else ""
            raise RuntimeError(f"All available Groq keys are rate-limited.{suffix}")
        raise RuntimeError("No usable Groq API key was available.")

    @staticmethod
    def _normalize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Preserve tool-call structure while ensuring normal content is serializable."""
        clean: list[dict[str, Any]] = []
        for raw in messages:
            role = str(raw.get("role", "user"))
            msg: dict[str, Any] = {"role": role}
            if role == "assistant" and raw.get("tool_calls"):
                msg["content"] = raw.get("content")
                msg["tool_calls"] = raw["tool_calls"]
            elif role == "tool":
                msg["tool_call_id"] = str(raw.get("tool_call_id", ""))
                if raw.get("name"):
                    msg["name"] = str(raw["name"])
                msg["content"] = redact_secrets(str(raw.get("content", "")))
            else:
                content = raw.get("content", "")
                if isinstance(content, (dict, list)):
                    content = json.dumps(content)
                msg["content"] = redact_secrets(str(content or ""))
            clean.append(msg)
        return clean

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._normalize_messages(messages),
            "temperature": self.temperature,
            "max_completion_tokens": int(max_tokens),
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        data = self._post(payload)
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("Groq returned an unexpected response shape.") from exc
        return {
            "role": "assistant",
            "content": message.get("content"),
            "tool_calls": message.get("tool_calls") or [],
            "finish_reason": data.get("choices", [{}])[0].get("finish_reason", ""),
        }

    def list_models(self) -> list[str]:
        order = self._request_order()
        if not order:
            raise RuntimeError("No Groq API key configured. Use /keys to add one.")
        last_error = ""
        for idx in order:
            key = self.cfg.get(f"groq_key_{idx}", "")
            try:
                response = requests.get(
                    MODELS_URL,
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    timeout=30,
                )
            except requests.RequestException as exc:
                raise RuntimeError(f"Groq network error: {exc}") from exc
            self._record_headers(idx, response)
            if response.status_code == 200:
                self.cfg["active_key_index"] = idx
                save_config(self.cfg)
                data = response.json().get("data", [])
                return sorted(str(item.get("id")) for item in data if item.get("id"))
            last_error = redact_secrets(response.text[:300])
        raise RuntimeError(f"Could not fetch Groq models: {last_error or 'all keys unavailable'}")
