"""Provider-neutral model contracts and normalized response structures."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, provider: str, code: str = "provider_error", retryable: bool = False):
        super().__init__(message)
        self.provider = provider
        self.code = code
        self.retryable = retryable


class ProviderCapabilityError(ProviderError):
    pass


@dataclass(frozen=True)
class ModelCapabilities:
    tool_calling: bool | None = None
    parallel_tool_calls: bool | None = None
    streaming: bool | None = None
    structured_output: bool | None = None
    context_window: int | None = None
    reasoning: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_calling": self.tool_calling,
            "parallel_tool_calls": self.parallel_tool_calls,
            "streaming": self.streaming,
            "structured_output": self.structured_output,
            "context_window": self.context_window,
            "reasoning": self.reasoning,
        }


@dataclass(frozen=True)
class ModelUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    @classmethod
    def from_mapping(cls, value: Any) -> "ModelUsage":
        raw = value if isinstance(value, dict) else {}
        input_tokens = int(raw.get("prompt_tokens", raw.get("input_tokens", 0)) or 0)
        output_tokens = int(raw.get("completion_tokens", raw.get("output_tokens", 0)) or 0)
        total = int(raw.get("total_tokens", input_tokens + output_tokens) or 0)
        return cls(input_tokens=max(0, input_tokens), output_tokens=max(0, output_tokens), total_tokens=max(0, total))

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass(frozen=True)
class ModelToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any] | None
    parse_error: str | None = None

    @classmethod
    def from_raw(cls, value: Any) -> "ModelToolCall":
        raw = value if isinstance(value, dict) else {}
        function = raw.get("function") if isinstance(raw.get("function"), dict) else {}
        raw_arguments = function.get("arguments", "{}")
        try:
            arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else dict(raw_arguments or {})
            if not isinstance(arguments, dict):
                raise ValueError("tool arguments must be an object")
            error = None
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            arguments = None
            error = str(exc)
        return cls(
            call_id=str(raw.get("id", "")),
            name=str(function.get("name", "")),
            arguments=arguments,
            parse_error=error,
        )

    def to_message_dict(self) -> dict[str, Any]:
        arguments = json.dumps(self.arguments or {}, separators=(",", ":"))
        return {
            "id": self.call_id,
            "type": "function",
            "function": {"name": self.name, "arguments": arguments},
        }


@dataclass
class ModelResponse:
    content: str | None
    tool_calls: list[ModelToolCall] = field(default_factory=list)
    finish_reason: str = ""
    usage: ModelUsage = field(default_factory=ModelUsage)
    provider: str = "unknown"
    model: str = "unknown"
    latency_ms: int = 0
    purpose: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": self.content,
            "tool_calls": [call.to_message_dict() for call in self.tool_calls],
            "finish_reason": self.finish_reason,
            "usage": self.usage.to_dict(),
            "provider": self.provider,
            "model": self.model,
            "latency_ms": self.latency_ms,
            "purpose": self.purpose,
            "metadata": dict(self.metadata),
        }

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


def normalize_model_response(value: Any, *, provider: str = "unknown", model: str = "unknown") -> ModelResponse:
    if isinstance(value, ModelResponse):
        return value
    raw = value if isinstance(value, dict) else {}
    return ModelResponse(
        content=raw.get("content"),
        tool_calls=[ModelToolCall.from_raw(call) for call in raw.get("tool_calls", [])],
        finish_reason=str(raw.get("finish_reason", "")),
        usage=ModelUsage.from_mapping(raw.get("usage", {})),
        provider=str(raw.get("provider", provider)),
        model=str(raw.get("model", model)),
        latency_ms=int(raw.get("latency_ms", 0) or 0),
        purpose=str(raw.get("purpose", "")),
    )


@runtime_checkable
class ModelProvider(Protocol):
    name: str
    model: str

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
        max_tokens: int = 4096,
    ) -> ModelResponse: ...

    def list_models(self) -> list[str]: ...

    def capabilities(self, model: str | None = None) -> ModelCapabilities: ...
