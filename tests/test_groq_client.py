import unittest
from unittest.mock import patch

from sable.groq_client import GroqClient


class FakeResponse:
    def __init__(self, status_code, data=None, headers=None, text=""):
        self.status_code = status_code
        self._data = data or {}
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._data


class KeyRotationTests(unittest.TestCase):
    @patch("sable.groq_client.save_config", lambda cfg: None)
    @patch("sable.groq_client.requests.post")
    def test_usage_is_charged_to_successful_rotated_key(self, post):
        post.side_effect = [
            FakeResponse(429, headers={"retry-after": "1"}),
            FakeResponse(200, data={
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"total_tokens": 123},
            }, headers={"x-ratelimit-remaining-tokens": "999"}),
        ]
        cfg = {
            "groq_key_1": "key-one",
            "groq_key_2": "key-two",
            "groq_key_3": "",
            "active_key_index": 1,
            "token_usage": {"1": 0, "2": 0, "3": 0},
            "rate_limits": {},
        }
        client = GroqClient(cfg, "test-model")
        result = client.complete([{"role": "user", "content": "hello"}])
        self.assertEqual(result["content"], "ok")
        self.assertEqual(result.usage.total_tokens, 123)
        self.assertEqual(result.provider, "groq")
        self.assertEqual(cfg["token_usage"]["1"], 0)
        self.assertEqual(cfg["token_usage"]["2"], 123)
        self.assertEqual(cfg["active_key_index"], 2)
