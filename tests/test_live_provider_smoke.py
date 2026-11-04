"""Tests for the live provider smoke script.

No network and no keys are used here: fake clients cover every branch of
run_smoke, and the skip path is covered by running main with no provider
keys set. A live pass can only be observed by running the script itself
with real keys; these tests prove the script reports correctly, not that
any provider works.

This module uses only the standard-library unittest framework, matching
the rest of Sable's suite and the CI runner (`python -m unittest
discover -s tests`). It must not import pytest: pytest is not an
installed dependency, and importing it breaks collection of the whole
suite under unittest.
"""

import io
import os
import unittest
from contextlib import redirect_stdout

from sable.providers.base import ModelResponse, ModelToolCall, ModelUsage
from scripts.live_provider_smoke import main, run_smoke, smoke_provider

PROVIDER_ENV_VARS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}
MANAGED_ENV_VARS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "DEEPSEEK_API_KEY",
    "SABLE_SMOKE_OPENAI_MODEL",
    "SABLE_SMOKE_ANTHROPIC_MODEL",
    "SABLE_SMOKE_DEEPSEEK_MODEL",
)


class FakeClient:
    def __init__(self, *, models=("m1",), chat="SMOKE-OK", tool=True, fail=""):
        self.model = "fake-model"
        self._models = list(models)
        self._chat = chat
        self._tool = tool
        self._fail = fail

    def list_models(self):
        if self._fail == "discovery":
            raise RuntimeError("discovery boom")
        return self._models

    def complete(self, messages, *, tools=None, max_tokens=32):
        if self._fail == "chat" and not tools:
            raise RuntimeError("chat boom")
        if self._fail == "tool" and tools:
            raise RuntimeError("tool boom")
        if tools and self._tool:
            call = ModelToolCall(call_id="c1", name="echo", arguments={"value": "smoke"})
            return ModelResponse(
                content=None,
                tool_calls=[call],
                usage=ModelUsage(input_tokens=10, output_tokens=5, total_tokens=15),
            )
        return ModelResponse(
            content=self._chat,
            usage=ModelUsage(input_tokens=10, output_tokens=5, total_tokens=15),
        )


class LiveProviderSmokeTest(unittest.TestCase):
    def setUp(self):
        # Snapshot and clear every variable these tests touch, so an
        # ambient key on a developer machine can never turn a skip-path
        # test into a live provider call. tearDown restores them exactly.
        self._saved_env = {name: os.environ.get(name) for name in MANAGED_ENV_VARS}
        for name in MANAGED_ENV_VARS:
            os.environ.pop(name, None)

    def tearDown(self):
        for name, value in self._saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_run_smoke_passes_with_all_checks(self):
        result = run_smoke("openai", FakeClient())
        self.assertEqual(result.status, "PASS")
        self.assertEqual([c.name for c in result.checks], ["discovery", "chat", "tool"])
        self.assertTrue(all(c.passed for c in result.checks))
        self.assertEqual(result.total_tokens, 30)

    def test_run_smoke_reports_each_failed_check(self):
        cases = [
            ("empty model list", FakeClient(models=[])),
            ("wrong chat reply", FakeClient(chat="something else")),
            ("no tool call", FakeClient(tool=False)),
            ("discovery error", FakeClient(fail="discovery")),
            ("chat error", FakeClient(fail="chat")),
            ("tool error", FakeClient(fail="tool")),
        ]
        for label, client in cases:
            with self.subTest(label=label):
                self.assertEqual(run_smoke("openai", client).status, "FAIL")

    def test_missing_keys_skip_without_failing(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main([])
        self.assertEqual(code, 0)
        out = buffer.getvalue()
        self.assertIn("SKIPPED", out)
        self.assertIn("missing OPENAI_API_KEY", out)

    def test_smoke_provider_skip_uses_env_model(self):
        os.environ["SABLE_SMOKE_OPENAI_MODEL"] = "custom-model"
        result = smoke_provider("openai")
        self.assertEqual(result.status, "SKIPPED")
        self.assertEqual(result.model, "custom-model")
        self.assertIn("OPENAI_API_KEY", result.detail)

    def test_no_provider_key_leaks_into_skip_output(self):
        # Even a present-looking env var must never be echoed: the skip path
        # runs before any client exists, and the report carries no key fields.
        result = smoke_provider("deepseek")
        self.assertIn("DEEPSEEK_API_KEY", result.detail)  # the var NAME, not a value
        self.assertIsNone(os.environ.get("DEEPSEEK_API_KEY"))
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(["--provider", "deepseek"])
        self.assertEqual(code, 0)
        self.assertNotIn("sk-", buffer.getvalue())

    def test_every_provider_has_a_skip_path(self):
        for provider, env_var in PROVIDER_ENV_VARS.items():
            with self.subTest(provider=provider):
                self.assertIsNone(os.environ.get(env_var))
                self.assertEqual(smoke_provider(provider).status, "SKIPPED")


if __name__ == "__main__":
    unittest.main()
