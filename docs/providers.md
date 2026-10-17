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

Please report provider failures with the provider name, model ID, HTTP status/error code, and a
redacted response body. Never include an API key in an issue.
