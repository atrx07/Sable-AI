import unittest
from unittest.mock import patch

from sable.groq_client import GroqClient
from sable.providers.base import ProviderError


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
            FakeResponse(
                200,
                data={
                    "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                    "usage": {"total_tokens": 123},
                },
                headers={"x-ratelimit-remaining-tokens": "999"},
            ),
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

    @patch("sable.groq_client.save_config", lambda cfg: None)
    @patch("sable.groq_client.time.sleep")
    @patch("sable.groq_client.requests.post")
    def test_rate_limit_waits_and_retries_key_cycle(self, post, sleep):
        post.side_effect = [
            FakeResponse(429, headers={"retry-after": "2"}),
            FakeResponse(429, headers={"retry-after": "2"}),
            FakeResponse(
                200,
                data={
                    "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                    "usage": {"total_tokens": 50},
                },
                headers={"x-ratelimit-remaining-tokens": "999"},
            ),
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
        self.assertEqual(result.usage.total_tokens, 50)
        sleep.assert_called_once_with(2.0)
        self.assertEqual(cfg["token_usage"]["1"], 50)
        self.assertEqual(cfg["token_usage"]["2"], 0)

    @patch("sable.groq_client.save_config", lambda cfg: None)
    @patch("sable.groq_client.time.sleep")
    @patch("sable.groq_client.requests.post")
    def test_rate_limit_gives_up_after_retries(self, post, sleep):
        post.side_effect = [FakeResponse(429, headers={"retry-after": "1"}) for _ in range(4)]
        cfg = {
            "groq_key_1": "key-one",
            "groq_key_2": "",
            "groq_key_3": "",
            "active_key_index": 1,
            "token_usage": {"1": 0, "2": 0, "3": 0},
            "rate_limits": {},
        }
        client = GroqClient(cfg, "test-model")
        with self.assertRaises(ProviderError) as caught:
            client.complete([{"role": "user", "content": "hello"}])
        self.assertEqual(caught.exception.code, "rate_limited")
        self.assertEqual(post.call_count, 4)
        self.assertEqual(sleep.call_count, 3)
        sleep.assert_called_with(1.0)

    def test_rate_limit_wait_parsing(self):
        wait_for = GroqClient._rate_limit_wait_seconds
        self.assertEqual(wait_for({"retry-after": "7"}), 7.0)
        self.assertEqual(
            wait_for({"retry-after": "bogus", "x-ratelimit-reset-tokens": "47.107s"}),
            47.107,
        )
        self.assertEqual(wait_for({"x-ratelimit-reset-tokens": "3.5s"}), 3.5)
        self.assertEqual(wait_for({}), 10.0)
        self.assertEqual(wait_for({"retry-after": "0"}), 10.0)
        self.assertEqual(wait_for({"retry-after": "999"}), 60.0)
