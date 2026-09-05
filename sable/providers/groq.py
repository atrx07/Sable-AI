"""Groq provider export; the legacy module path remains supported."""

from ..groq_client import GroqClient

GroqProvider = GroqClient

__all__ = ["GroqProvider"]
