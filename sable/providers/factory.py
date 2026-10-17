"""Provider construction helpers."""

from __future__ import annotations

from ..config import get_provider


def create_non_groq_provider(cfg: dict, model: str, temperature: float = 0.2):
    provider = get_provider(cfg)
    if provider == "openai":
        from .openai import OpenAIProvider

        return OpenAIProvider(cfg, model, temperature)
    if provider == "anthropic":
        from .anthropic import AnthropicProvider

        return AnthropicProvider(cfg, model, temperature)
    if provider == "deepseek":
        from .deepseek import DeepSeekProvider

        return DeepSeekProvider(cfg, model, temperature)
    raise ValueError(f"Unsupported non-Groq provider: {provider}")
