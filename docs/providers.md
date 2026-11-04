# Model providers

Sable supports four direct model providers:

| Provider | Credential | Transport | Model discovery |
| --- | --- | --- | --- |
| Groq | `GROQ_API_KEY` or `/keys` slots | OpenAI-compatible Chat Completions | `GET /models` |
| OpenAI | `OPENAI_API_KEY` or `/keys` | OpenAI Chat Completions | `GET /v1/models` |
| Anthropic | `ANTHROPIC_API_KEY` or `/keys` | Anthropic Messages API | `GET /v1/models` |
| DeepSeek | `DEEPSEEK_API_KEY` or `/keys` | OpenAI-compatible Chat Completions | `GET /models` |

Groq remains the default so existing installations continue to work without a migration step.

## Switching providers

Run `/config`. The two model prompts remain first for backwards compatibility with older scripted
CLI interactions. The provider prompt follows them:

```text
main_model:
fast_model:
provider [groq/openai/anthropic/deepseek] (groq):
```

After changing provider, run `/keys`, then `/models`, then `/config` again if you need to replace
the main/fast model IDs with models available to your account.

Groq keeps its three-key rotation. OpenAI, Anthropic, and DeepSeek currently use one configured
credential each. Environment variables are resolved at use time and are not copied into the
persisted Sable config.

## Tool calling

Sable's internal provider contract stays provider-neutral:

- OpenAI and DeepSeek use function tools in their OpenAI-compatible chat schema.
- Anthropic tool definitions are translated to `input_schema`, and `tool_use` / `tool_result`
  blocks are normalized to Sable's `ModelToolCall` representation.
- Groq keeps the existing native function-call path.

Provider model capabilities can change independently of Sable. A model returned by `/models` is not
automatically guaranteed to support every Sable feature.

## Validation status

The OpenAI, Anthropic, and DeepSeek adapters are covered by mocked unit tests and the normal Sable
CI suite. They have **not** yet received live paid-API smoke tests. Treat the adapters as beta until
real credentials exercise chat, tool calling, model discovery, rate limits, and provider-specific
edge cases in production.

### Live smoke tests (opt-in, manual only)

`scripts/live_provider_smoke.py` runs one tiny, bounded check per provider
through Sable's own adapters — model discovery, a chat reply, and one tool
call — and reports PASS, FAIL, or SKIPPED per provider with total tokens
used. It is not part of CI and never gates a release by itself:

```bash
OPENAI_API_KEY=... ANTHROPIC_API_KEY=... DEEPSEEK_API_KEY=... \
  python -m scripts.live_provider_smoke
python -m scripts.live_provider_smoke --provider openai --json smoke.json
```

Rules, by design: a missing key means SKIPPED, never a failure or a spend;
exactly one attempt per check, no retries (retries are how a flaky adapter
burns quota); keys come from the environment only and are never printed or
written to the report; model IDs are overridable per provider
(`SABLE_SMOKE_OPENAI_MODEL`, `SABLE_SMOKE_ANTHROPIC_MODEL`,
`SABLE_SMOKE_DEEPSEEK_MODEL`; defaults `gpt-4o-mini`,
`claude-3-5-haiku-latest`, `deepseek-chat`). A beta label is lifted for a
provider only after a recorded pass for that provider, by a separate docs
change — the existence of this script does not lift it.

Please report provider failures with the provider name, model ID, HTTP status/error code, and a
redacted response body. Never include an API key in an issue.
