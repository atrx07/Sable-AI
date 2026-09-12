"""Offline scripted provider that drives real Sable agent paths deterministically."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from ..config import redact_secrets
from ..providers import (
    ModelCapabilities,
    ModelResponse,
    ModelToolCall,
    ModelUsage,
    ProviderError,
)


@dataclass(frozen=True)
class ScriptedRequest:
    message_count: int
    last_role: str
    last_content: str
    tools_enabled: bool
    tool_choice: str
    max_tokens: int


def _tool_call(value: Any, index: int) -> ModelToolCall:
    if isinstance(value, ModelToolCall):
        return value
    if not isinstance(value, dict):
        raise ValueError("scripted tool call must be an object")
    arguments = value.get("arguments", {})
    parse_error = value.get("parse_error")
    if arguments is not None and not isinstance(arguments, dict):
        raise ValueError("scripted tool arguments must be an object or null")
    return ModelToolCall(
        call_id=str(value.get("call_id", f"script-call-{index}")),
        name=str(value.get("name", "")),
        arguments=dict(arguments) if isinstance(arguments, dict) else None,
        parse_error=str(parse_error) if parse_error else None,
    )


def _response(value: Any, index: int, model: str) -> ModelResponse:
    if isinstance(value, ModelResponse):
        return value
    if not isinstance(value, dict):
        raise ValueError("scripted provider response must be an object")
    calls = value.get("tool_calls", [])
    if not isinstance(calls, list):
        raise ValueError("scripted tool_calls must be a list")
    usage = value.get("usage", {})
    return ModelResponse(
        content=value.get("content"),
        tool_calls=[_tool_call(item, call_index) for call_index, item in enumerate(calls, 1)],
        finish_reason=str(value.get("finish_reason", "tool_calls" if calls else "stop")),
        usage=ModelUsage.from_mapping(usage),
        provider="scripted",
        model=model,
        latency_ms=max(0, int(value.get("latency_ms", 0) or 0)),
        purpose=str(value.get("purpose", "MAIN_REASONING")),
        metadata={"script_index": index},
    )


class ScriptedProvider:
    """A no-network provider that returns a finite, predeclared response sequence."""

    name = "scripted"

    def __init__(self, responses: Iterable[dict[str, Any] | ModelResponse], *, model: str = "sable-eval-scripted-v1"):
        self.model = model
        self._responses = [_response(value, index, model) for index, value in enumerate(responses)]
        self._position = 0
        self.requests: list[ScriptedRequest] = []

    @property
    def remaining(self) -> int:
        return len(self._responses) - self._position

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
        max_tokens: int = 4096,
    ) -> ModelResponse:
        last = messages[-1] if messages else {}
        self.requests.append(ScriptedRequest(
            message_count=len(messages),
            last_role=str(last.get("role", "")),
            last_content=redact_secrets(str(last.get("content", "")))[:1000],
            tools_enabled=bool(tools),
            tool_choice=str(tool_choice),
            max_tokens=max(1, int(max_tokens)),
        ))
        if self._position >= len(self._responses):
            raise ProviderError(
                "Scripted evaluation provider exhausted its declared responses.",
                provider=self.name,
                code="script_exhausted",
                retryable=False,
            )
        response = self._responses[self._position]
        self._position += 1
        return response

    def list_models(self) -> list[str]:
        return [self.model]

    def capabilities(self, model: str | None = None) -> ModelCapabilities:
        return ModelCapabilities(
            tool_calling=True,
            parallel_tool_calls=True,
            streaming=False,
            structured_output=False,
            context_window=32768,
            reasoning=False,
        )


__all__ = ["ScriptedProvider", "ScriptedRequest"]
