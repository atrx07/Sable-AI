"""Shared OpenAI-compatible Chat Completions transport."""

from __future__ import annotations

import json
import time
from typing import Any

import requests

from ..config import get_provider_key, redact_secrets
from .base import ModelCapabilities, ModelResponse, ModelToolCall, ModelUsage, ProviderError


class OpenAICompatibleProvider:
    name = "openai-compatible"
    base_url = ""
    token_field = "max_tokens"

    def __init__(self, cfg: dict, model: str, temperature: float = 0.2):
        self.cfg = cfg
        self.model = model
        self.temperature = float(temperature)

    @property
    def chat_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"

    @property
    def models_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/models"

    def _key(self) -> str:
        return get_provider_key(self.cfg, self.name)

    def _headers(self) -> dict[str, str]:
        key = self._key()
        if not key:
            raise ProviderError(
                f"No {self.name} API key configured. Use /keys to add one.",
                provider=self.name,
                code="missing_key",
            )
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    @staticmethod
    def _normalize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
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

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = self._headers()
        try:
            response = requests.post(self.chat_url, headers=headers, json=payload, timeout=120)
        except requests.RequestException as exc:
            raise ProviderError(
                f"{self.name} network error: {redact_secrets(str(exc))}",
                provider=self.name,
                code="network_error",
                retryable=True,
            ) from exc

        if response.status_code in (401, 403):
            raise ProviderError(
                f"{self.name} rejected the configured API key.",
                provider=self.name,
                code="authentication",
            )
        if response.status_code == 429:
            raise ProviderError(
                f"{self.name} rate limit reached.",
                provider=self.name,
                code="rate_limited",
                retryable=True,
            )
        if response.status_code != 200:
            body = redact_secrets(response.text[:500])
            raise ProviderError(
                f"{self.name} API error {response.status_code}: {body}",
                provider=self.name,
                code=f"http_{response.status_code}",
                retryable=response.status_code >= 500,
            )
        try:
            data = response.json()
        except (ValueError, TypeError) as exc:
            raise ProviderError(
                f"{self.name} returned a non-JSON response.",
                provider=self.name,
                code="invalid_response",
            ) from exc
        if not isinstance(data, dict):
            raise ProviderError(
                f"{self.name} returned an invalid response.",
                provider=self.name,
                code="invalid_response",
            )
        return data

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
        max_tokens: int = 4096,
    ) -> ModelResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._normalize_messages(messages),
            "temperature": self.temperature,
            self.token_field: int(max_tokens),
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice

        started = time.monotonic()
        data = self._post(payload)
        latency_ms = max(0, int((time.monotonic() - started) * 1000))
        try:
            choice = data["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                f"{self.name} returned an unexpected response shape.",
                provider=self.name,
                code="invalid_response",
            ) from exc

        return ModelResponse(
            content=message.get("content"),
            tool_calls=[ModelToolCall.from_raw(call) for call in message.get("tool_calls") or []],
            finish_reason=str(choice.get("finish_reason", "")),
            usage=ModelUsage.from_mapping(data.get("usage", {})),
            provider=self.name,
            model=str(data.get("model") or self.model),
            latency_ms=latency_ms,
        )

    def list_models(self) -> list[str]:
        headers = self._headers()
        try:
            response = requests.get(self.models_url, headers=headers, timeout=30)
        except requests.RequestException as exc:
            raise ProviderError(
                f"{self.name} network error: {redact_secrets(str(exc))}",
                provider=self.name,
                code="network_error",
                retryable=True,
            ) from exc
        if response.status_code in (401, 403):
            raise ProviderError(
                f"{self.name} rejected the configured API key.",
                provider=self.name,
                code="authentication",
            )
        if response.status_code != 200:
            body = redact_secrets(response.text[:300])
            raise ProviderError(
                f"Could not fetch {self.name} models ({response.status_code}): {body}",
                provider=self.name,
                code=f"http_{response.status_code}",
                retryable=response.status_code >= 500,
            )
        try:
            data = response.json().get("data", [])
        except (AttributeError, ValueError, TypeError) as exc:
            raise ProviderError(
                f"{self.name} returned an invalid model catalogue.",
                provider=self.name,
                code="invalid_response",
            ) from exc
        return sorted(
            str(item.get("id")) for item in data if isinstance(item, dict) and item.get("id")
        )

    def capabilities(self, model: str | None = None) -> ModelCapabilities:
        return ModelCapabilities(
            tool_calling=True,
            parallel_tool_calls=True,
            streaming=True,
            structured_output=None,
            context_window=None,
            reasoning=None,
        )
