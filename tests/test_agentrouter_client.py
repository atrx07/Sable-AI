import os
import unittest
from unittest.mock import patch

from sable.agentrouter_client import AgentRouterClient
from sable.config import get_active_key, get_provider
from sable.groq_client import GroqClient
from sable.providers import ModelResponse, ModelUsage, ProviderError


class FakeResponse:
    def __init__(self, status_code, data=None, text="", headers=None):
        self.status_code = status_code
        self._data = data if data is not None else {}
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._data


def config():
    return {
        "provider": "agentrouter",
        "agentrouter_key": "sk-" + "A" * 40,
        "agentrouter_base_url": "https://co.agentrouter.org/v1",
        "main_model": "gpt-test",
        "fast_model": "fast-test",
    }


class AgentRouterProviderTests(unittest.TestCase):
    def test_legacy_groq_constructor_dispatches_to_agentrouter(self):
        client = GroqClient(config(), "gpt-test")
        self.assertIsInstance(client, AgentRouterClient)
        self.assertEqual(client.name, "agentrouter")
        self.assertEqual(client.chat_url, "https://co.agentrouter.org/v1/chat/completions")

    @patch("sable.agentrouter_client.requests.post")
    def test_response_tool_calls_and_usage_are_normalized(self, post):
        post.return_value = FakeResponse(
            200,
            data={
                "model": "gpt-test-routed",
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
                "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
            },
        )
        result = AgentRouterClient(config(), "gpt-test").complete(
            [{"role": "user", "content": "inspect"}],
            tools=[{"type": "function", "function": {"name": "read_file"}}],
            max_tokens=321,
        )
        self.assertIsInstance(result, ModelResponse)
        self.assertEqual(result.provider, "agentrouter")
        self.assertEqual(result.model, "gpt-test-routed")
        self.assertEqual(result.usage, ModelUsage(5, 7, 12))
        self.assertEqual(result.tool_calls[0].name, "read_file")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["max_tokens"], 321)
        self.assertEqual(payload["tool_choice"], "auto")
        self.assertNotIn("max_completion_tokens", payload)

    @patch("sable.agentrouter_client.requests.post")
    def test_402_is_structured_as_retryable_quota_unavailable(self, post):
        post.return_value = FakeResponse(402, text="model allocation exhausted")
        with self.assertRaises(ProviderError) as caught:
            AgentRouterClient(config(), "gpt-test").complete([{"role": "user", "content": "hello"}])
        self.assertEqual(caught.exception.code, "quota_unavailable")
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(caught.exception.provider, "agentrouter")

    @patch("sable.agentrouter_client.requests.get")
    def test_live_model_catalogue_uses_openai_compatible_models_endpoint(self, get):
        get.return_value = FakeResponse(
            200,
            data={"data": [{"id": "z-model"}, {"id": "a-model"}, {"object": "model"}]},
        )
        models = AgentRouterClient(config(), "gpt-test").list_models()
        self.assertEqual(models, ["a-model", "z-model"])
        self.assertEqual(get.call_args.args[0], "https://co.agentrouter.org/v1/models")

    def test_provider_and_environment_key_resolution(self):
        cfg = {"provider": "AGENTROUTER", "agentrouter_key": ""}
        with patch.dict(os.environ, {"AGENTROUTER_API_KEY": "env-key"}, clear=False):
            self.assertEqual(get_provider(cfg), "agentrouter")
            self.assertEqual(get_active_key(cfg), ("env-key", 1))


if __name__ == "__main__":
    unittest.main()
