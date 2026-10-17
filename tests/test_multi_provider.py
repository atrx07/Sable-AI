import os
import unittest
from unittest.mock import patch

import requests

from sable import config
from sable.groq_client import GroqClient
from sable.providers.anthropic import AnthropicProvider
from sable.providers.base import ModelResponse, ProviderError
from sable.providers.deepseek import DeepSeekProvider
from sable.providers.openai import OpenAIProvider


class FakeResponse:
    def __init__(self, status_code=200, data=None, text="", headers=None):
        self.status_code = status_code
        self._data = data
        self.text = text
        self.headers = headers or {}

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


class ProviderConfigTests(unittest.TestCase):
    def test_provider_key_uses_environment_without_persisting_it(self):
        cfg = {"provider": "openai", "openai_key": ""}
        with patch.dict(os.environ, {"OPENAI_API_KEY": "env-openai"}, clear=False):
            self.assertEqual(config.get_provider_key(cfg), "env-openai")
            self.assertEqual(config.get_active_key(cfg), ("env-openai", 1))
        self.assertEqual(cfg["openai_key"], "")

    def test_unknown_provider_falls_back_to_groq(self):
        self.assertEqual(config.get_provider({"provider": "mystery"}), "groq")

    def test_anthropic_secret_pattern_is_redacted(self):
        secret = "sk-ant-" + ("A" * 32)
        self.assertNotIn(secret, config.redact_secrets(f"token={secret}"))


class ProviderFactoryTests(unittest.TestCase):
    def test_legacy_groq_constructor_dispatches_openai(self):
        client = GroqClient({"provider": "openai", "openai_key": "x"}, "model")
        self.assertIsInstance(client, OpenAIProvider)

    def test_legacy_groq_constructor_dispatches_anthropic(self):
        client = GroqClient({"provider": "anthropic", "anthropic_key": "x"}, "model")
        self.assertIsInstance(client, AnthropicProvider)

    def test_legacy_groq_constructor_dispatches_deepseek(self):
        client = GroqClient({"provider": "deepseek", "deepseek_key": "x"}, "model")
        self.assertIsInstance(client, DeepSeekProvider)


class OpenAICompatibleProviderTests(unittest.TestCase):
    @patch("sable.providers.openai_compat.requests.post")
    def test_openai_tool_call_and_usage_are_normalized(self, post):
        post.return_value = FakeResponse(
            data={
                "model": "gpt-test",
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": '{"path":"a.py"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            }
        )
        client = OpenAIProvider({"provider": "openai", "openai_key": "key"}, "gpt-test")
        result = client.complete(
            [{"role": "user", "content": "inspect"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            max_tokens=123,
        )
        self.assertIsInstance(result, ModelResponse)
        self.assertEqual(result.provider, "openai")
        self.assertEqual(result.tool_calls[0].name, "read_file")
        self.assertEqual(result.tool_calls[0].arguments, {"path": "a.py"})
        self.assertEqual(result.usage.total_tokens, 8)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["max_completion_tokens"], 123)
        self.assertEqual(payload["tool_choice"], "auto")

    @patch("sable.providers.openai_compat.requests.post")
    def test_deepseek_uses_max_tokens(self, post):
        post.return_value = FakeResponse(
            data={
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"input_tokens": 2, "output_tokens": 1},
            }
        )
        client = DeepSeekProvider(
            {"provider": "deepseek", "deepseek_key": "key"}, "deepseek-flash"
        )
        result = client.complete([{"role": "user", "content": {"task": "hi"}}], max_tokens=77)
        self.assertEqual(result.content, "ok")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["max_tokens"], 77)
        self.assertNotIn("max_completion_tokens", payload)
        self.assertIn('"task": "hi"', payload["messages"][0]["content"])

    @patch("sable.providers.openai_compat.requests.get")
    def test_openai_model_discovery(self, get):
        get.return_value = FakeResponse(data={"data": [{"id": "b"}, {"id": "a"}]})
        client = OpenAIProvider({"provider": "openai", "openai_key": "key"}, "unused")
        self.assertEqual(client.list_models(), ["a", "b"])

    @patch("sable.providers.openai_compat.requests.post")
    def test_authentication_error_is_structured(self, post):
        post.return_value = FakeResponse(status_code=401, data={})
        client = DeepSeekProvider({"provider": "deepseek", "deepseek_key": "bad"}, "model")
        with self.assertRaises(ProviderError) as caught:
            client.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(caught.exception.code, "authentication")
        self.assertFalse(caught.exception.retryable)

    @patch("sable.providers.openai_compat.requests.post")
    def test_rate_limit_is_retryable(self, post):
        post.return_value = FakeResponse(status_code=429, data={})
        client = OpenAIProvider({"provider": "openai", "openai_key": "key"}, "model")
        with self.assertRaises(ProviderError) as caught:
            client.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(caught.exception.code, "rate_limited")
        self.assertTrue(caught.exception.retryable)

    @patch("sable.providers.openai_compat.requests.post")
    def test_network_error_is_retryable(self, post):
        post.side_effect = requests.RequestException("offline")
        client = OpenAIProvider({"provider": "openai", "openai_key": "key"}, "model")
        with self.assertRaises(ProviderError) as caught:
            client.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(caught.exception.code, "network_error")
        self.assertTrue(caught.exception.retryable)

    def test_missing_key_fails_before_network(self):
        client = OpenAIProvider({"provider": "openai", "openai_key": ""}, "model")
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ProviderError) as caught:
                client.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(caught.exception.code, "missing_key")
        self.assertTrue(client.capabilities().tool_calling)


class AnthropicProviderTests(unittest.TestCase):
    @patch("sable.providers.anthropic.requests.post")
    def test_messages_and_tool_use_are_translated(self, post):
        post.return_value = FakeResponse(
            data={
                "model": "claude-test",
                "content": [
                    {"type": "text", "text": "Checking."},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "read_file",
                        "input": {"path": "a.py"},
                    },
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 10, "output_tokens": 4},
            }
        )
        client = AnthropicProvider(
            {"provider": "anthropic", "anthropic_key": "key"}, "claude-test"
        )
        result = client.complete(
            [
                {"role": "system", "content": "Be precise."},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "old-call",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path":"old.py"}',
                            },
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "old-call", "content": "old contents"},
                {"role": "user", "content": "Now inspect a.py"},
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "description": "Read one file",
                        "parameters": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                        },
                    },
                }
            ],
            tool_choice="required",
        )
        self.assertEqual(result.provider, "anthropic")
        self.assertEqual(result.content, "Checking.")
        self.assertEqual(result.tool_calls[0].name, "read_file")
        self.assertEqual(result.tool_calls[0].arguments, {"path": "a.py"})
        self.assertEqual(result.usage.total_tokens, 14)

        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["system"], "Be precise.")
        self.assertEqual(payload["tool_choice"], {"type": "any"})
        self.assertEqual(payload["tools"][0]["input_schema"]["type"], "object")
        self.assertEqual(payload["messages"][0]["content"][0]["type"], "tool_use")
        self.assertEqual(payload["messages"][1]["content"][0]["type"], "tool_result")
        self.assertEqual(payload["messages"][1]["content"][1]["type"], "text")

    @patch("sable.providers.anthropic.requests.get")
    def test_anthropic_model_discovery(self, get):
        get.return_value = FakeResponse(data={"data": [{"id": "claude-b"}, {"id": "claude-a"}]})
        client = AnthropicProvider(
            {"provider": "anthropic", "anthropic_key": "key"}, "unused"
        )
        self.assertEqual(client.list_models(), ["claude-a", "claude-b"])
        self.assertEqual(get.call_args.kwargs["headers"]["anthropic-version"], "2023-06-01")

    @patch("sable.providers.anthropic.requests.post")
    def test_anthropic_rate_limit_is_retryable(self, post):
        post.return_value = FakeResponse(status_code=429, data={})
        client = AnthropicProvider(
            {"provider": "anthropic", "anthropic_key": "key"}, "model"
        )
        with self.assertRaises(ProviderError) as caught:
            client.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(caught.exception.code, "rate_limited")
        self.assertTrue(caught.exception.retryable)

    @patch("sable.providers.anthropic.requests.post")
    def test_anthropic_server_error_is_retryable_and_redacted(self, post):
        secret = "sk-ant-" + ("B" * 32)
        post.return_value = FakeResponse(status_code=500, text=f"failed {secret}", data={})
        client = AnthropicProvider(
            {"provider": "anthropic", "anthropic_key": "key"}, "model"
        )
        with self.assertRaises(ProviderError) as caught:
            client.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(caught.exception.code, "http_500")
        self.assertTrue(caught.exception.retryable)
        self.assertNotIn(secret, str(caught.exception))

    def test_tool_choice_none_is_omitted(self):
        self.assertIsNone(AnthropicProvider._tool_choice("none"))
        self.assertEqual(AnthropicProvider._tool_choice("weird"), {"type": "auto"})
        self.assertTrue(AnthropicProvider({}, "model").capabilities().tool_calling)


if __name__ == "__main__":
    unittest.main()
