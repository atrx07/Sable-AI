import unittest
from unittest.mock import patch

from sable.groq_client import GroqClient
from sable.providers import (
    ModelCapabilities,
    ModelResponse,
    ModelRouter,
    ModelToolCall,
    ModelUsage,
    ProviderCapabilityError,
    ProviderError,
    RoutePurpose,
)


class FakeResponse:
    def __init__(self, status_code, data=None, text="", headers=None):
        self.status_code = status_code
        self._data = data or {}
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._data


def config():
    return {
        "groq_key_1": "key-one",
        "groq_key_2": "",
        "groq_key_3": "",
        "active_key_index": 1,
        "token_usage": {"1": 0, "2": 0, "3": 0},
        "rate_limits": {},
    }


class GroqProviderTests(unittest.TestCase):
    @patch("sable.groq_client.save_config", lambda cfg: None)
    @patch("sable.groq_client.requests.post")
    def test_response_tool_calls_and_usage_are_normalized(self, post):
        post.return_value = FakeResponse(200, data={
            "choices": [{
                "message": {"content": None, "tool_calls": [{
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path":"a.py"}'},
                }]},
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        })
        result = GroqClient(config(), "text-model").complete(
            [{"role": "user", "content": "inspect"}], tools=[{"type": "function"}]
        )
        self.assertIsInstance(result, ModelResponse)
        self.assertEqual(result.provider, "groq")
        self.assertEqual(result.model, "text-model")
        self.assertEqual(result.usage, ModelUsage(11, 7, 18))
        self.assertEqual(result.tool_calls[0], ModelToolCall("call-1", "read_file", {"path": "a.py"}))
        self.assertEqual(result["tool_calls"][0]["function"]["name"], "read_file")

    @patch("sable.groq_client.requests.post")
    def test_known_audio_model_rejects_tools_before_request(self, post):
        client = GroqClient(config(), "whisper-large-v3")
        with self.assertRaises(ProviderCapabilityError):
            client.complete([{"role": "user", "content": "edit"}], tools=[{"type": "function"}])
        post.assert_not_called()
        self.assertFalse(client.capabilities().tool_calling)

    @patch("sable.groq_client.requests.post")
    def test_provider_error_is_structured_and_redacted(self, post):
        secret = "gsk_" + "A" * 30
        post.return_value = FakeResponse(500, text=f"failure {secret}")
        with self.assertRaises(ProviderError) as caught:
            GroqClient(config(), "text-model").complete([{"role": "user", "content": "hello"}])
        self.assertEqual(caught.exception.code, "http_500")
        self.assertTrue(caught.exception.retryable)
        self.assertNotIn(secret, str(caught.exception))


class FakeProvider:
    name = "fake"

    def __init__(self, model, *, fail=False):
        self.model = model
        self.fail = fail
        self.calls = []

    def complete(self, messages, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise ProviderError("helper unavailable", provider=self.name, retryable=True)
        return {"content": self.model, "tool_calls": [], "usage": {"total_tokens": 3}}

    def list_models(self):
        return [self.model]

    def capabilities(self, model=None):
        return ModelCapabilities(tool_calling=True)


class ModelRouterTests(unittest.TestCase):
    def test_main_and_fast_purposes_use_distinct_models(self):
        main = FakeProvider("main-model")
        fast = FakeProvider("fast-model")
        router = ModelRouter(main, fast)
        main_response = router.complete(RoutePurpose.MAIN_REASONING, [{"role": "user", "content": "code"}])
        fast_response = router.complete(RoutePurpose.FAST_CLASSIFICATION, [{"role": "user", "content": "classify"}])
        self.assertEqual(main_response.model, "main-model")
        self.assertEqual(fast_response.model, "fast-model")
        self.assertEqual(main_response.purpose, "MAIN_REASONING")
        self.assertEqual(fast_response.purpose, "FAST_CLASSIFICATION")

    def test_fast_route_cannot_receive_tools(self):
        router = ModelRouter(FakeProvider("main"), FakeProvider("fast"))
        with self.assertRaises(ProviderCapabilityError):
            router.complete(
                RoutePurpose.FAST_CONTEXT_SUMMARY,
                [{"role": "user", "content": "summarize"}],
                tools=[{"type": "function"}],
            )

    def test_fast_failure_uses_deterministic_fallback(self):
        router = ModelRouter(FakeProvider("main"), FakeProvider("fast", fail=True))
        response = router.fast_or_fallback(
            RoutePurpose.FAST_RESULT_SUMMARY,
            [{"role": "user", "content": "summarize"}],
            fallback="deterministic summary",
        )
        self.assertEqual(response.content, "deterministic summary")
        self.assertEqual(response.finish_reason, "fallback")
        self.assertIn("helper unavailable", response.metadata["fallback_reason"])
