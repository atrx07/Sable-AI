"""Groq Chat Completions client with native tool calling and key rotation."""

from __future__ import annotations

import json
import math
import time
from typing import Any, Callable

import requests

from .config import (
    configured_key_indices,
    get_active_key,
    get_configured_key,
    get_provider,
    redact_secrets,
    rotate_to_next_key,
    save_config,
)
from .providers.base import (
    ModelCapabilities,
    ModelResponse,
    ModelToolCall,
    ModelUsage,
    ProviderCapabilityError,
    ProviderError,
)

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
CHAT_URL = f"{GROQ_BASE_URL}/chat/completions"
MODELS_URL = f"{GROQ_BASE_URL}/models"

# How long _post waits out a 429 before retrying the key cycle. The free tier
# caps tokens per minute, so a burst of turns can throttle every key at once;
# the server's Retry-After is honoured up to this cap, a few times per request.
MAX_RATE_LIMIT_RETRIES = 3
MAX_RATE_LIMIT_WAIT_SECONDS = 60.0
DEFAULT_RATE_LIMIT_WAIT_SECONDS = 10.0


class GroqClient:
    name = "groq"

    def __new__(cls, cfg: dict, model: str, temperature: float = 0.2):
        """Preserve the legacy constructor while dispatching configured providers."""
        if cls is GroqClient and get_provider(cfg) != "groq":
            from .providers.factory import create_non_groq_provider

            return create_non_groq_provider(cfg, model, temperature)
        return super().__new__(cls)

    def __init__(self, cfg: dict, model: str, temperature: float = 0.2):
        self.cfg = cfg
        self.model = model
        self.temperature = float(temperature)
        # Optional runtime hook; never write provider progress to machine-readable stdout.
        self.on_rate_limit_wait: Callable[[str], None] | None = None

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
        snapshot = {
            key: response.headers.get(key, "")
            for key in keys
            if response.headers.get(key) is not None
        }
        self.cfg.setdefault("rate_limits", {})[str(idx)] = snapshot

    def _record_usage(self, idx: int, data: dict) -> None:
        usage = data.get("usage", {}) if isinstance(data, dict) else {}
        total = int(usage.get("total_tokens", 0) or 0)
        self.cfg.setdefault("token_usage", {}).setdefault(str(idx), 0)
        self.cfg["token_usage"][str(idx)] += total
        self.cfg["active_key_index"] = idx
        save_config(self.cfg)

    @staticmethod
    def _rate_limit_wait_seconds(headers: dict) -> float:
        """Seconds to wait after a 429, from Retry-After or the token reset window."""
        raw = str(headers.get("retry-after") or "").strip()
        wait: float | None = None
        try:
            wait = float(raw) if raw else None
        except ValueError:
            wait = None
        if wait is None or not math.isfinite(wait) or wait <= 0:
            reset = str(headers.get("x-ratelimit-reset-tokens") or "").strip().rstrip("s")
            try:
                wait = float(reset) if reset else None
            except ValueError:
                wait = None
        if wait is None or not math.isfinite(wait) or wait <= 0:
            wait = DEFAULT_RATE_LIMIT_WAIT_SECONDS
        return min(wait, MAX_RATE_LIMIT_WAIT_SECONDS)

    def _post(self, payload: dict) -> dict:
        order = self._request_order()
        if not order:
            raise ProviderError(
                "No Groq API key configured. Use /keys to add one.",
                provider=self.name,
                code="missing_key",
            )

        # The free tier caps tokens per minute, so a burst of turns can throttle
        # every key at once. Wait out the server's Retry-After and retry the whole
        # key cycle a few times before giving up, instead of aborting the run.
        retries = 0
        while True:
            rate_limited: list[tuple[int, dict]] = []
            auth_failed: list[int] = []

            for idx in order:
                key = get_configured_key(self.cfg, idx)
                headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
                try:
                    response = requests.post(CHAT_URL, headers=headers, json=payload, timeout=120)
                except requests.RequestException as exc:
                    raise ProviderError(
                        f"Groq network error: {redact_secrets(str(exc))}",
                        provider=self.name,
                        code="network_error",
                        retryable=True,
                    ) from exc

                self._record_headers(idx, response)

                if response.status_code == 429:
                    rate_limited.append((idx, {k.lower(): v for k, v in response.headers.items()}))
                    continue
                if response.status_code in (401, 403):
                    auth_failed.append(idx)
                    continue
                if response.status_code != 200:
                    body = redact_secrets(response.text[:500])
                    raise ProviderError(
                        f"Groq API error {response.status_code}: {body}",
                        provider=self.name,
                        code=f"http_{response.status_code}",
                        retryable=response.status_code >= 500,
                    )

                try:
                    data = response.json()
                except (ValueError, TypeError) as exc:
                    raise ProviderError(
                        "Groq returned a non-JSON response.",
                        provider=self.name,
                        code="invalid_response",
                    ) from exc
                if not isinstance(data, dict):
                    raise ProviderError(
                        "Groq returned an invalid response.",
                        provider=self.name,
                        code="invalid_response",
                    )
                self._record_usage(idx, data)
                return data

            # Rotate the configured active slot for the next request even when all failed.
            if order:
                rotate_to_next_key(self.cfg, order[-1])

            if len(auth_failed) == len(order):
                raise ProviderError(
                    "All configured Groq keys were rejected. Check them with /keys.",
                    provider=self.name,
                    code="authentication",
                )
            if rate_limited:
                if retries < MAX_RATE_LIMIT_RETRIES:
                    retries += 1
                    wait = self._rate_limit_wait_seconds(rate_limited[0][1])
                    if self.on_rate_limit_wait is not None:
                        self.on_rate_limit_wait(
                            f"Groq is throttling requests: waiting {wait:g}s "
                            f"before retrying ({retries}/{MAX_RATE_LIMIT_RETRIES})."
                        )
                    time.sleep(wait)
                    continue
                waits = [
                    headers.get("retry-after", "")
                    for _, headers in rate_limited
                    if headers.get("retry-after")
                ]
                suffix = f" Retry after about {waits[0]}s." if waits else ""
                raise ProviderError(
                    f"All available Groq keys are rate-limited.{suffix}",
                    provider=self.name,
                    code="rate_limited",
                    retryable=True,
                )
            raise ProviderError(
                "No usable Groq API key was available.", provider=self.name, code="unavailable"
            )

    def capabilities(self, model: str | None = None) -> ModelCapabilities:
        selected = str(model or self.model).lower()
        is_audio = any(marker in selected for marker in ("whisper", "speech", "tts"))
        return ModelCapabilities(
            tool_calling=False if is_audio else True,
            parallel_tool_calls=False if is_audio else True,
            streaming=True,
            structured_output=None,
            context_window=None,
            reasoning=True if "gpt-oss" in selected else None,
        )

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
    ) -> ModelResponse:
        capabilities = self.capabilities()
        if tools and capabilities.tool_calling is False:
            raise ProviderCapabilityError(
                f"Groq model '{self.model}' does not support the required tool calling.",
                provider=self.name,
                code="tool_calling_unsupported",
            )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._normalize_messages(messages),
            "temperature": self.temperature,
            "max_completion_tokens": int(max_tokens),
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        started = time.monotonic()
        data = self._post(payload)
        latency_ms = max(0, int((time.monotonic() - started) * 1000))
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                "Groq returned an unexpected response shape.",
                provider=self.name,
                code="invalid_response",
            ) from exc
        return ModelResponse(
            content=message.get("content"),
            tool_calls=[ModelToolCall.from_raw(call) for call in message.get("tool_calls") or []],
            finish_reason=str(data.get("choices", [{}])[0].get("finish_reason", "")),
            usage=ModelUsage.from_mapping(data.get("usage", {})),
            provider=self.name,
            model=self.model,
            latency_ms=latency_ms,
        )

    def list_models(self) -> list[str]:
        order = self._request_order()
        if not order:
            raise RuntimeError("No Groq API key configured. Use /keys to add one.")
        last_error = ""
        for idx in order:
            key = get_configured_key(self.cfg, idx)
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
