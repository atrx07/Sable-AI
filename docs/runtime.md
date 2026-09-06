# Runtime state and provider routing

Each natural-language request becomes a `RuntimeTask` with an explicit lifecycle:

`CREATED → DISCOVER → CONTEXT → PLAN/EXECUTE → VERIFY/REPAIR → REPORT`

The transition table is enforced in `sable/runtime.py`; invalid transitions and
terminal-task mutation raise `RuntimeStateError`. A task records its transaction
ID, repository baseline, selected context, model/tool counts, token usage,
latency, verification result, changed files, termination reason, and bounded
events. Terminal statuses distinguish successful completion, verification
failure, policy/limit blocking, and unexpected aborts.

Model calls use the provider protocol in `sable/providers/base.py`. `ModelRouter`
keeps main reasoning on the configured main model and sends bounded context
compression to the configured fast model. Fast routes never receive tools; if a
fast provider fails, Sable uses a deterministic local fallback.

Provider responses are normalized into `ModelResponse` and `ModelUsage`, so the
runtime can report usage consistently without exposing request headers or API
keys. Groq remains the production provider in this release.
