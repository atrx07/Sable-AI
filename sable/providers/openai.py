"""OpenAI provider using the OpenAI-compatible Chat Completions surface."""

from __future__ import annotations

from .openai_compat import OpenAICompatibleProvider


class OpenAIProvider(OpenAICompatibleProvider):
    name = "openai"
    base_url = "https://api.openai.com/v1"
    token_field = "max_completion_tokens"
