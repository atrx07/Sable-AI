"""Anthropic Messages API provider with native tool-use normalization."""

from __future__ import annotations

import json
import time
from typing import Any

import requests

from ..config import get_provider_key, redact_secrets
from .base import ModelCapabilities, ModelResponse, ModelToolCall, ModelUsage, ProviderError

ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, cfg: dict, model: str, temperature: float = 0.2):
        self.cfg = cfg
        self.model = model
        self.temperature = float(temperature)

    def _key(self) -> str:
        return get_provider_key(self.cfg, self.name)

    def _headers(self) -> dict[str, str]:
        key = self._key()
        if not key:
            raise ProviderError(
                "No anthropic API key configured. Use /keys to add one.",
                provider=self.name,
                code="missing_key",
            )
        return {
            "x-api-key": key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

    @staticmethod
    def _tool_arguments(call: dict[str, Any]) -> dict[str, Any]:
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        raw = function.get("arguments", "{}")
        if isinstance(raw, dict):
            return raw
        try:
            parsed = json.loads(str(raw))
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _append_message(
        output: list[dict[str, Any]], role: str, blocks: list[dict[str, Any]]
    ) -> None:
        if output and output[-1].get("role") == role:
            existing = output[-1].get("content")
            if not isinstance(existing, list):
                existing = [{"type": "text", "text": str(existing or "")}]
                output[-1]["content"] = existing
            existing.extend(blocks)
            return
        output.append({"role": role, "content": blocks})

    @classmethod
    def _normalize_messages(
        cls, messages: list[dict[str, Any]]
    ) -> tuple[str, list[dict[str, Any]]]:
        system_parts: list[str] = []
        output: list[dict[str, Any]] = []
        for raw in messages:
            role = str(raw.get("role", "user"))
            content = raw.get("content", "")
            if role == "system":
                if isinstance(content, (dict, list)):
                    content = json.dumps(content)
                system_parts.append(redact_secrets(str(content or "")))
                continue

            if role == "assistant":
                blocks: list[dict[str, Any]] = []
                if content:
                    if isinstance(content, (dict, list)):
                        content = json.dumps(content)
                    blocks.append({"type": "text", "text": redact_secrets(str(content))})
                for call in raw.get("tool_calls") or []:
                    if not isinstance(call, dict):
                        continue
                    function = call.get("function")
                    if not isinstance(function, dict):
                        continue
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": str(call.get("id", "")),
                            "name": str(function.get("name", "")),
                            "input": cls._tool_arguments(call),
                        }
                    )
                cls._append_message(output, "assistant", blocks or [{"type": "text", "text": ""}])
                continue

            if role == "tool":
                block = {
                    "type": "tool_result",
                    "tool_use_id": str(raw.get("tool_call_id", "")),
                    "content": redact_secrets(str(content or "")),
                }
                cls._append_message(output, "user", [block])
                continue

            if isinstance(content, (dict, list)):
                content = json.dumps(content)
            cls._append_message(
                output,
                "user",
                [{"type": "text", "text": redact_secrets(str(content or ""))}],
            )
        return "\n\n".join(part for part in system_parts if part), output

    @staticmethod
    def _normalize_tools(tools: list[dict] | None) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for tool in tools or []:
            function = tool.get("function") if isinstance(tool, dict) else None
            if not isinstance(function, dict):
                continue
            item: dict[str, Any] = {
                "name": str(function.get("name", "")),
                "input_schema": function.get("parameters") or {"type": "object", "properties": {}},
            }
            if function.get("description"):
                item["description"] = str(function["description"])
            normalized.append(item)
        return normalized

    @staticmethod
    def _tool_choice(value: str) -> dict[str, str] | None:
        lowered = str(value or "auto").lower()
        if lowered == "auto":
            return {"type": "auto"}
        if lowered in {"required", "any"}:
            return {"type": "any"}
        if lowered == "none":
            return None
        return {"type": "auto"}

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = requests.post(
                f"{ANTHROPIC_BASE_URL}/messages",
                headers=self._headers(),
                json=payload,
                timeout=120,
            )
        except requests.RequestException as exc:
            raise ProviderError(
                f"anthropic network error: {redact_secrets(str(exc))}",
                provider=self.name,
                code="network_error",
                retryable=True,
            ) from exc

        if response.status_code in (401, 403):
            raise ProviderError(
                "anthropic rejected the configured API key.",
                provider=self.name,
                code="authentication",
            )
        if response.status_code == 429:
            raise ProviderError(
                "anthropic rate limit reached.",
                provider=self.name,
                code="rate_limited",
                retryable=True,
            )
        if response.status_code != 200:
            body = redact_secrets(response.text[:500])
            raise ProviderError(
                f"anthropic API error {response.status_code}: {body}",
                provider=self.name,
                code=f"http_{response.status_code}",
                retryable=response.status_code >= 500,
            )
        try:
            data = response.json()
        except (ValueError, TypeError) as exc:
            raise ProviderError(
                "anthropic returned a non-JSON response.",
                provider=self.name,
                code="invalid_response",
            ) from exc
        if not isinstance(data, dict):
            raise ProviderError(
                "anthropic returned an invalid response.",
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
        system, normalized = self._normalize_messages(messages)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": normalized,
            "max_tokens": int(max_tokens),
            "temperature": self.temperature,
        }
        if system:
            payload["system"] = system
        native_tools = self._normalize_tools(tools)
        if native_tools:
            payload["tools"] = native_tools
            choice = self._tool_choice(tool_choice)
            if choice is not None:
                payload["tool_choice"] = choice

        started = time.monotonic()
        data = self._post(payload)
        latency_ms = max(0, int((time.monotonic() - started) * 1000))

        text_parts: list[str] = []
        tool_calls: list[ModelToolCall] = []
        for block in data.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text_parts.append(str(block.get("text", "")))
            elif block.get("type") == "tool_use":
                raw_input = block.get("input")
                arguments = raw_input if isinstance(raw_input, dict) else {}
                tool_calls.append(
                    ModelToolCall(
                        call_id=str(block.get("id", "")),
                        name=str(block.get("name", "")),
                        arguments=arguments,
                    )
                )

        usage = data.get("usage", {})
        return ModelResponse(
            content="".join(text_parts) or None,
            tool_calls=tool_calls,
            finish_reason=str(data.get("stop_reason", "")),
            usage=ModelUsage.from_mapping(usage),
            provider=self.name,
            model=str(data.get("model") or self.model),
            latency_ms=latency_ms,
        )

    def list_models(self) -> list[str]:
        try:
            response = requests.get(
                f"{ANTHROPIC_BASE_URL}/models",
                headers=self._headers(),
                timeout=30,
            )
        except requests.RequestException as exc:
            raise ProviderError(
                f"anthropic network error: {redact_secrets(str(exc))}",
                provider=self.name,
                code="network_error",
                retryable=True,
            ) from exc
        if response.status_code in (401, 403):
            raise ProviderError(
                "anthropic rejected the configured API key.",
                provider=self.name,
                code="authentication",
            )
        if response.status_code != 200:
            body = redact_secrets(response.text[:300])
            raise ProviderError(
                f"Could not fetch anthropic models ({response.status_code}): {body}",
                provider=self.name,
                code=f"http_{response.status_code}",
                retryable=response.status_code >= 500,
            )
        try:
            data = response.json().get("data", [])
        except (AttributeError, ValueError, TypeError) as exc:
            raise ProviderError(
                "anthropic returned an invalid model catalogue.",
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
