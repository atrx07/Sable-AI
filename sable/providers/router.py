"""Purpose-based main/fast model routing without autonomous sub-agents."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..config import redact_secrets
from .base import (
    ModelProvider,
    ModelResponse,
    ProviderCapabilityError,
    normalize_model_response,
)


class RoutePurpose(str, Enum):
    MAIN_REASONING = "MAIN_REASONING"
    FAST_CLASSIFICATION = "FAST_CLASSIFICATION"
    FAST_CONTEXT_SUMMARY = "FAST_CONTEXT_SUMMARY"
    FAST_RESULT_SUMMARY = "FAST_RESULT_SUMMARY"

    @property
    def is_fast(self) -> bool:
        return self is not RoutePurpose.MAIN_REASONING


@dataclass(frozen=True)
class RouteDecision:
    purpose: RoutePurpose
    provider: str
    model: str


class ModelRouter:
    def __init__(self, main_provider: ModelProvider, fast_provider: ModelProvider | None = None):
        self.main_provider = main_provider
        self.fast_provider = fast_provider or main_provider

    @staticmethod
    def _provider_name(provider: Any) -> str:
        return str(getattr(provider, "name", provider.__class__.__name__.lower()))

    @staticmethod
    def _model_name(provider: Any) -> str:
        return str(getattr(provider, "model", "unknown"))

    @property
    def main_model(self) -> str:
        return self._model_name(self.main_provider)

    @property
    def fast_model(self) -> str:
        return self._model_name(self.fast_provider)

    @property
    def provider_name(self) -> str:
        return self._provider_name(self.main_provider)

    def decision(self, purpose: RoutePurpose) -> tuple[ModelProvider, RouteDecision]:
        provider = self.fast_provider if purpose.is_fast else self.main_provider
        return provider, RouteDecision(purpose, self._provider_name(provider), self._model_name(provider))

    def complete(
        self,
        purpose: RoutePurpose,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
        max_tokens: int = 4096,
    ) -> ModelResponse:
        provider, decision = self.decision(purpose)
        if purpose.is_fast and tools:
            raise ProviderCapabilityError(
                "Fast helper routes cannot receive tools or mutate files.",
                provider=decision.provider,
                code="fast_tools_forbidden",
            )
        response = normalize_model_response(
            provider.complete(messages, tools=tools, tool_choice=tool_choice, max_tokens=max_tokens),
            provider=decision.provider,
            model=decision.model,
        )
        response.purpose = purpose.value
        return response

    def fast_or_fallback(
        self,
        purpose: RoutePurpose,
        messages: list[dict[str, Any]],
        *,
        fallback: str,
        max_tokens: int = 800,
    ) -> ModelResponse:
        if not purpose.is_fast:
            raise ValueError("fast_or_fallback requires a fast helper purpose")
        try:
            return self.complete(purpose, messages, tools=None, tool_choice="none", max_tokens=max_tokens)
        except Exception as exc:
            return ModelResponse(
                content=fallback,
                finish_reason="fallback",
                provider="deterministic",
                model="fallback",
                purpose=purpose.value,
                metadata={"fallback_reason": redact_secrets(str(exc))[:300]},
            )
