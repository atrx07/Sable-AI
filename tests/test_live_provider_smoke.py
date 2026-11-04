"""Tests for the live provider smoke script.

No network and no keys are used here: fake clients cover every branch of
run_smoke, and the skip path is covered by running main with no provider
keys set. A live pass can only be observed by running the script itself
with real keys; these tests prove the script reports correctly, not that
any provider works.
"""

import os

import pytest

from sable.providers.base import ModelResponse, ModelToolCall, ModelUsage
from scripts.live_provider_smoke import main, run_smoke, smoke_provider


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


def test_run_smoke_passes_with_all_checks():
    result = run_smoke("openai", FakeClient())
    assert result.status == "PASS"
    assert [c.name for c in result.checks] == ["discovery", "chat", "tool"]
    assert all(c.passed for c in result.checks)
    assert result.total_tokens == 30


def test_run_smoke_reports_each_failed_check():
    assert run_smoke("openai", FakeClient(models=[])).status == "FAIL"
    assert run_smoke("openai", FakeClient(chat="something else")).status == "FAIL"
    assert run_smoke("openai", FakeClient(tool=False)).status == "FAIL"
    assert run_smoke("openai", FakeClient(fail="discovery")).status == "FAIL"
    assert run_smoke("openai", FakeClient(fail="chat")).status == "FAIL"
    assert run_smoke("openai", FakeClient(fail="tool")).status == "FAIL"


def test_missing_keys_skip_without_failing(monkeypatch, capsys):
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "SKIPPED" in out
    assert "missing OPENAI_API_KEY" in out


def test_smoke_provider_skip_uses_env_model(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("SABLE_SMOKE_OPENAI_MODEL", "custom-model")
    result = smoke_provider("openai")
    assert result.status == "SKIPPED"
    assert result.model == "custom-model"
    assert "OPENAI_API_KEY" in result.detail


def test_no_provider_key_leaks_into_skip_output(monkeypatch, capsys):
    # Even a present-looking env var must never be echoed: the skip path
    # runs before any client exists, and the report carries no key fields.
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    result = smoke_provider("deepseek")
    assert "DEEPSEEK_API_KEY" in result.detail  # the var NAME, not a value
    assert os.environ.get("DEEPSEEK_API_KEY") is None
    assert main(["--provider", "deepseek"]) == 0
    assert "sk-" not in capsys.readouterr().out


@pytest.mark.parametrize("provider", ["openai", "anthropic", "deepseek"])
def test_every_provider_has_a_skip_path(monkeypatch, provider):
    env = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
    }[provider]
    monkeypatch.delenv(env, raising=False)
    assert smoke_provider(provider).status == "SKIPPED"
