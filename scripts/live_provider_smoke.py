"""Cheap, opt-in live smoke tests for Sable's beta providers.

This script exercises Sable's own provider adapters (not raw HTTP), so a pass
actually proves the adapter works end to end: model discovery, chat, and one
tool call. It is deliberately NOT part of the ordinary test suite or CI:

- No key for a provider means SKIPPED, never a failure and never a spend.
- Exactly one attempt per check. No retries: a retry loop is how a flaky
  adapter burns quota (see the closed GPT-OSS patch-dialect issue, where
  retries burned quota until a rate-limit abort).
- Tiny prompts and tiny token caps, with total tokens reported per provider.
  Prices are not estimated here: providers reprice independently, so the
  report records tokens, not money.
- Keys come from the environment only and are never printed, logged, or
  written to the report. Model IDs are printed; they are not secrets.
- The script never gates a release by itself. A beta label is lifted per
  provider only after a recorded pass, by a separate docs change.

Usage (from the repository root, with the checkout installed):

    OPENAI_API_KEY=... ANTHROPIC_API_KEY=... DEEPSEEK_API_KEY=... \\
        python -m scripts.live_provider_smoke
    python -m scripts.live_provider_smoke --provider openai --json smoke.json

Model defaults are overridable per provider, because model IDs are
provider-controlled and change independently of Sable:

    SABLE_SMOKE_OPENAI_MODEL=gpt-4o-mini
    SABLE_SMOKE_ANTHROPIC_MODEL=claude-3-5-haiku-latest
    SABLE_SMOKE_DEEPSEEK_MODEL=deepseek-chat

Exit codes: 0 when nothing failed (skips are not failures), 1 when any
configured provider failed a check.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any

from sable.config import redact_secrets
from sable.providers.base import ModelResponse, ModelUsage
from sable.providers.factory import create_non_groq_provider

PROVIDERS: dict[str, dict[str, str]] = {
    "openai": {
        "env": "OPENAI_API_KEY",
        "model_env": "SABLE_SMOKE_OPENAI_MODEL",
        "default_model": "gpt-4o-mini",
    },
    "anthropic": {
        "env": "ANTHROPIC_API_KEY",
        "model_env": "SABLE_SMOKE_ANTHROPIC_MODEL",
        "default_model": "claude-3-5-haiku-latest",
    },
    "deepseek": {
        "env": "DEEPSEEK_API_KEY",
        "model_env": "SABLE_SMOKE_DEEPSEEK_MODEL",
        "default_model": "deepseek-chat",
    },
}

ECHO_TOOL = {
    "type": "function",
    "function": {
        "name": "echo",
        "description": "Echo back the supplied value.",
        "parameters": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
    },
}


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class ProviderResult:
    provider: str
    model: str
    status: str  # PASS, FAIL, or SKIPPED
    checks: list[CheckResult] = field(default_factory=list)
    total_tokens: int = 0
    detail: str = ""


def _usage_total(response: ModelResponse) -> int:
    usage: ModelUsage = response.usage or ModelUsage()
    return int(usage.total_tokens or 0)


def _redacted_error(exc: Exception) -> str:
    return redact_secrets(str(exc))[:300]


def run_smoke(provider: str, client: Any) -> ProviderResult:
    """Run discovery, chat, and tool checks exactly once each."""
    model = str(getattr(client, "model", "unknown"))
    result = ProviderResult(provider=provider, model=model, status="PASS")
    try:
        models = client.list_models()
        if models:
            result.checks.append(CheckResult("discovery", True, f"{len(models)} models listed"))
        else:
            result.checks.append(CheckResult("discovery", False, "empty model list"))
    except Exception as exc:  # noqa: BLE001 - smoke must report, not crash
        result.checks.append(CheckResult("discovery", False, _redacted_error(exc)))
    try:
        response = client.complete(
            [{"role": "user", "content": "Reply with exactly: SMOKE-OK"}],
            max_tokens=32,
        )
        result.total_tokens += _usage_total(response)
        content = str(response.content or "")
        if "SMOKE-OK" in content.upper():
            result.checks.append(CheckResult("chat", True, content[:80]))
        else:
            result.checks.append(CheckResult("chat", False, f"unexpected reply: {content[:80]!r}"))
    except Exception as exc:  # noqa: BLE001 - smoke must report, not crash
        result.checks.append(CheckResult("chat", False, _redacted_error(exc)))
    try:
        response = client.complete(
            [{"role": "user", "content": "Call the echo tool with value 'smoke'."}],
            tools=[ECHO_TOOL],
            max_tokens=64,
        )
        result.total_tokens += _usage_total(response)
        calls = list(response.tool_calls or [])
        echo = [c for c in calls if c.name == "echo"]
        if echo and (echo[0].arguments or {}).get("value") == "smoke":
            result.checks.append(CheckResult("tool", True, "echo(smoke) called"))
        else:
            result.checks.append(
                CheckResult(
                    "tool",
                    False,
                    f"no echo(smoke) call; got {[c.name for c in calls]!r}",
                )
            )
    except Exception as exc:  # noqa: BLE001 - smoke must report, not crash
        result.checks.append(CheckResult("tool", False, _redacted_error(exc)))
    if any(not check.passed for check in result.checks):
        result.status = "FAIL"
    return result


def smoke_provider(provider: str) -> ProviderResult:
    spec = PROVIDERS[provider]
    if not os.environ.get(spec["env"]):
        return ProviderResult(
            provider=provider,
            model=os.environ.get(spec["model_env"], spec["default_model"]),
            status="SKIPPED",
            detail=f"missing {spec['env']}; set it to run this provider",
        )
    model = os.environ.get(spec["model_env"], spec["default_model"])
    client = create_non_groq_provider({"provider": provider}, model)
    return run_smoke(provider, client)


def report(results: list[ProviderResult]) -> dict[str, Any]:
    return {
        "providers": [
            {
                "provider": r.provider,
                "model": r.model,
                "status": r.status,
                "detail": r.detail,
                "total_tokens": r.total_tokens,
                "checks": [
                    {"name": c.name, "passed": c.passed, "detail": c.detail} for c in r.checks
                ],
            }
            for r in results
        ]
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live smoke tests for beta providers")
    parser.add_argument("--provider", action="append", choices=sorted(PROVIDERS))
    parser.add_argument("--json", dest="json_path", default="")
    args = parser.parse_args(argv)
    selected = args.provider or sorted(PROVIDERS)
    results = [smoke_provider(provider) for provider in selected]
    for result in results:
        print(f"{result.provider} ({result.model}): {result.status}")
        if result.detail:
            print(f"  {result.detail}")
        for check in result.checks:
            mark = "PASS" if check.passed else "FAIL"
            print(f"  {check.name}: {mark} — {check.detail}")
        if result.status != "SKIPPED":
            print(f"  total_tokens: {result.total_tokens}")
    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as handle:
            json.dump(report(results), handle, indent=2)
            handle.write("\n")
    return 1 if any(r.status == "FAIL" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
