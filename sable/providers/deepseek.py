"""DeepSeek provider using its OpenAI-compatible Chat Completions API."""

from __future__ import annotations

from .openai_compat import OpenAICompatibleProvider


class DeepSeekProvider(OpenAICompatibleProvider):
    name = "deepseek"
    base_url = "https://api.deepseek.com"
    token_field = "max_tokens"
