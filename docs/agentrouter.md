# AgentRouter provider

Sable can use AgentRouter as an alternative model provider through AgentRouter's OpenAI-compatible API.

## Configure it

1. Start Sable and run `/config`.
2. Set `provider` to `agentrouter`.
3. Set the main and fast model IDs. If you do not know the current IDs yet, keep the existing values temporarily, finish `/config`, then run `/keys` and `/models` to fetch AgentRouter's live catalogue before returning to `/config`.
4. Run `/keys` and enter the AgentRouter API key when prompted.
5. Run `/models` to verify credentials and inspect the current catalogue.

Groq remains the default provider for existing installations.

## Environment variables

Instead of storing the AgentRouter key in `~/.sable/config.json`, set:

```sh
export AGENTROUTER_API_KEY="your-key"
```

The default OpenAI-compatible base URL is:

```text
https://co.agentrouter.org/v1
```

It can be overridden in `/config` or with:

```sh
export AGENTROUTER_BASE_URL="https://co.agentrouter.org/v1"
```

Environment-provided keys are resolved at runtime and are not copied into Sable's persisted config.

## Runtime behavior

- Sable sends Chat Completions requests to `<base>/chat/completions`.
- `/models` reads the live catalogue from `<base>/models`; do not assume a model remains available just because it appeared previously.
- Tool-call responses are normalized into Sable's provider-neutral `ModelResponse` structures.
- HTTP `402` is reported as `quota_unavailable`, since it can represent an unavailable allocation/quota for the requested routed model.
- HTTP `429` is reported as a retryable rate-limit error.
- AgentRouter model capabilities are intentionally treated as unknown unless proven at runtime because the routed catalogue is dynamic.

Sable does not currently fail over from AgentRouter to Groq when a main-model request fails. Fast helper routes still use Sable's existing deterministic fallback behavior.

## Secrets and privacy

Keep API keys out of project files and prompts. Sable continues to redact known secret patterns and blocks its own credential/state paths from model context, but requests sent through AgentRouter may ultimately be processed by downstream model providers. Review the provider's current terms and privacy documentation before sending sensitive content.
