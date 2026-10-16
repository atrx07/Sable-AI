"""AgentRouter OpenAI-compatible Chat Completions provider."""

from __future__ import annotations

import json
import os
import time
from typing import Any

import requests

from .config import get_agentrouter_key, redact_secrets
from .providers.base import (
    ModelCapabilities,
    ModelResponse,
    ModelToolCall,
    ModelUsage,
    ProviderCapabilityError,
    ProviderError,
)

AGENTROUTER_DEFAULT_BASE_URL = "https://co.agentrouter.org/v1"


class AgentRouterClient:
    """Use AgentRouter through its OpenAI-compatible API surface."""

    name = "agentrouter"

    def __init__(self, cfg: dict, model: str, temperature: float = 0.2):
        self.cfg = cfg
        self.model = model
        self.temperature = float(temperature)
        configured = str(
            os.environ.get("AGENTROUTER_BASE_URL")
            or cfg.get("agentrouter_base_url")
            or AGENTROUTER_DEFAULT_BASE_URL
        ).strip()
        self.base_url = configured.rstrip("/")
        self.chat_url = f"{self.base_url}/chat/completions"
        self.models_url = f"{self.base_url}/models"

    def _key(self) -> str:
        return get_agentrouter_key(self.cfg)

    def _headers(self) -> dict[str, str]:
        key = self._key()
        if not key:
            raise ProviderError(
                "No AgentRouter API key configured. Use /keys or AGENTROUTER_API_KEY.",
                provider=self.name,
                code="missing_key",
            )
        return {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "Sable-AI/2.0",
        }

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

    def capabilities(self, model: str | None = None) -> ModelCapabilities:
        # AgentRouter's catalogue changes independently of Sable, including the
        # capabilities of individual routed models. Unknown properties remain
        # None rather than pretending every model supports the same features.
        return ModelCapabilities(
            tool_calling=None,
            parallel_tool_calls=None,
            streaming=None,
            structured_output=None,
            context_window=None,
            reasoning=None,
        )

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = requests.post(
                self.chat_url,
                headers=self._headers(),
                json=payload,
                timeout=120,
            )
        except requests.RequestException as exc:
            raise ProviderError(
                f"AgentRouter network error: {redact_secrets(str(exc))}",
                provider=self.name,
                code="network_error",
                retryable=True,
            ) from exc

        if response.status_code == 200:
            try:
                data = response.json()
            except (ValueError, TypeError) as exc:
                raise ProviderError(
                    "AgentRouter returned a non-JSON response.",
                    provider=self.name,
                    code="invalid_response",
                ) from exc
            if not isinstance(data, dict):
                raise ProviderError(
                    "AgentRouter returned an invalid response.",
                    provider=self.name,
                    code="invalid_response",
                )
            return data

        body = redact_secrets(response.text[:500])
        if response.status_code in (401, 403):
            raise ProviderError(
                f"AgentRouter authentication/client rejection: {body or response.status_code}",
                provider=self.name,
                code="authentication",
            )
        if response.status_code == 402:
            raise ProviderError(
                f"AgentRouter quota or model allocation unavailable: {body or 'HTTP 402'}",
                provider=self.name,
                code="quota_unavailable",
                retryable=True,
            )
        if response.status_code == 429:
            retry_after = response.headers.get("retry-after", "")
            suffix = f" Retry after about {retry_after}s." if retry_after else ""
            raise ProviderError(
                f"AgentRouter rate limit reached.{suffix}",
                provider=self.name,
                code="rate_limited",
                retryable=True,
            )
        raise ProviderError(
            f"AgentRouter API error {response.status_code}: {body}",
            provider=self.name,
            code=f"http_{response.status_code}",
            retryable=response.status_code >= 500,
        )

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
        max_tokens: int = 4096,
    ) -> ModelResponse:
        capabilities = self.capabilities()
        if tools and capabilities.tool_calling is False:
            raise ProviderCapabilityError(
                f"AgentRouter model '{self.model}' does not support the required tool calling.",
                provider=self.name,
                code="tool_calling_unsupported",
            )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._normalize_messages(messages),
            "temperature": self.temperature,
            "max_tokens": int(max_tokens),
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice

        started = time.monotonic()
        data = self._request(payload)
        latency_ms = max(0, int((time.monotonic() - started) * 1000))
        try:
            choice = data["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                "AgentRouter returned an unexpected response shape.",
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
        try:
            response = requests.get(self.models_url, headers=self._headers(), timeout=30)
        except requests.RequestException as exc:
            raise ProviderError(
                f"AgentRouter network error: {redact_secrets(str(exc))}",
                provider=self.name,
                code="network_error",
                retryable=True,
            ) from exc

        if response.status_code != 200:
            body = redact_secrets(response.text[:300])
            code = (
                "authentication"
                if response.status_code in (401, 403)
                else f"http_{response.status_code}"
            )
            raise ProviderError(
                f"Could not fetch AgentRouter models ({response.status_code}): {body}",
                provider=self.name,
                code=code,
                retryable=response.status_code in (402, 429) or response.status_code >= 500,
            )
        try:
            payload = response.json()
        except (ValueError, TypeError) as exc:
            raise ProviderError(
                "AgentRouter returned a non-JSON model catalogue.",
                provider=self.name,
                code="invalid_response",
            ) from exc
        data = payload.get("data", []) if isinstance(payload, dict) else []
        return sorted(
            str(item.get("id")) for item in data if isinstance(item, dict) and item.get("id")
        )
