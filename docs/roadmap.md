# Sable development roadmap

The original v2 roadmap is complete. These milestones describe the engineering
history; they are not separate editions that users must install.
The [evidence map](evidence.md) links the implemented foundation to source,
tests, scenario catalogs, and workflow configuration. Completion here refers to
those repository deliverables, not a public release or general reliability claim.

| Milestone | Completed outcome |
|---|---|
| M0 — bounded agent correctness | Sequential tool loop, explicit budgets, workspace path policy, safer commands, and protected Git defaults |
| M1 — runtime/security hardening | Atomic writes, credential boundaries, failure handling, and regression coverage |
| M2 — transactional editing | Bounded pre-mutation snapshots, persisted transaction metadata, conflict-aware undo, and recovery |
| M3 — runtime/context/observability | Validated task lifecycle, provider routing, repository-aware context, sessions, summaries, and traces |
| M4 — capability and execution controls | Typed capabilities, exact-action approvals, native/PRoot backends, environment policy, and truthful guarantees |
| M5 — verification engine | Deterministic discovery/planning, QUICK/AFFECTED/FULL scopes, failure classification, bounded repair, and integrity checks |
| M6 — product CLI and automation | Existing-workspace CLI, stable presentation, one-shot JSON/exit codes, doctor, and cancellation handling |
| M7 — evaluation | Synthetic deterministic/adversarial/resilience scenarios, committed baseline, CI gate, and opt-in live mode |
| M8 — OSS and release engineering | Packaging, quality/security gates, contribution policy, artifact validation, and safely gated release workflow |
| M9 — showcase and launch readiness | Product-first README, architecture diagrams, reproducible demos, release/launch drafts, and final claim audit |

## Current state

Sable's [source version](../sable/_version.py) is 2.0.0. The repository includes
[quality checks](quality.md) and [release validation](releasing.md); readiness for
publication depends on passing those checks on the exact candidate and completing
the owner checklist. A version number is not proof of publication. The owner must
still approve the public version, configure external release protections and trusted publishing as desired,
finalize a dated changelog section, create a tag, and deliberately dispatch enabled
publication jobs. Until then, source installation is the supported acquisition path.

The [README](../README.md) is the product entry point. Architecture, security,
evaluation, quality, release, and demo evidence live in their focused guides rather
than in the roadmap.

## Future roadmap

The items below are directional priorities, not promises, release dates, or claims
about current functionality. Future work should preserve Sable's existing runtime
policy, transaction, verification, evaluation, and release guarantees, or explicitly
version any incompatible contract.

All 17 items below are **proposed**. The numbered groups express relative horizons,
not committed schedules. Their completion criteria are suggested review gates,
not existing test results. An item can build on a shipped primitive without being
implemented as a whole.

## Suggested delivery order

| Workstream | Proposed dependency / first slice |
|---|---|
| Providers and local inference (1–3) | Add one provider adapter and its contract tests before adaptive cross-provider routing; verify a local-only path separately |
| Context and editing (4, 15) | Measure existing Python AST selection and patch behavior before adding parsers or strategies |
| Durable work (5, 12) | Specify safe task checkpoints and restart behavior before a background queue |
| Execution and extension (7, 8) | Define each backend/plugin's authority and evidence contract before enabling it |
| Client interfaces (6, 9–11) | Keep the current CLI contract stable; define authentication and runtime API boundaries before networked or IDE clients |
| Measurement and orchestration (13, 14, 16, 17) | Add trustworthy cost/provenance/reliability evidence before using it for routing or multi-agent decisions |

These dependencies are engineering recommendations. They do not add features or
change the near-, mid-, and longer-term scope below. Live evaluations should expand
alongside new providers and routing, with model IDs, repetitions, failures, skips,
usage, and host details kept distinct from deterministic acceptance results.

### Near-term — provider independence and runtime intelligence

1. **Multi-provider support**
   - Current foundation: [provider-neutral response contracts](../sable/providers/base.py)
     and [main/fast routing](../sable/providers/router.py); Groq is the only concrete
     production adapter in [the provider package](../sable/providers).
   - Generalize the provider layer so Groq is one backend rather than a product-level
     dependency.
   - Add first-class support for additional hosted providers such as OpenAI, Anthropic,
     and Gemini where their APIs and tool contracts fit Sable's runtime model.
   - Support OpenAI-compatible endpoints so local gateways and compatible providers can
     be integrated without provider-specific logic leaking into the runtime.
   - Keep credentials, rate limits, model metadata, and provider errors isolated behind
     the provider abstraction.
   - Proposed completion check: one additional adapter passes shared tool-call,
     malformed-response, usage, error, and credential-redaction tests; a separately
     labelled live run establishes integration with that provider.

2. **Local and offline model execution**
   - Current boundary: local tools and deterministic evaluations run on the host;
     normal model inference still uses Groq. Local-first is not an offline-model claim.
   - Add integrations for local runtimes such as Ollama or compatible local endpoints.
   - Allow an explicitly offline Sable workflow that does not require a hosted inference
     provider.
   - Preserve the same capability, transaction, trace, and verification controls for
     local models as for hosted models.
   - Proposed completion check: a task uses only an explicitly selected local endpoint
     without hosted credentials or hosted fallback, while existing policy and
     verification tests pass. Document the endpoint's own network assumptions.

3. **Role-aware and adaptive model routing**
   - Current foundation: `RoutePurpose` names main and fast helper purposes, but
     [routing](../sable/providers/router.py) selects between configured main/fast
     providers. Fast-helper errors return a deterministic fallback; that is not
     failover to another production provider.
   - Move beyond a simple main/fast split toward explicit runtime roles such as planner,
     executor, repair, context, and summarization.
   - Route using model capabilities, tool-call reliability, context limits, latency,
     provider health, and owner-defined cost budgets.
   - Add bounded provider fallback for outages or rate limits without silently weakening
     task policy.
   - Proposed completion check: replayable routing decisions record why a role chose
     a model; outage and budget tests show bounded fallback with unchanged authority.

4. **Semantic code intelligence**
   - Current foundation: [ContextEngine](../sable/context/engine.py) already parses
     Python AST symbols/imports and uses bounded selection heuristics.
   - Add language-aware parsing, likely through Tree-sitter or equivalent structured
     analysis.
   - Improve symbol lookup, import/call relationships, semantic chunking, changed-symbol
     impact analysis, and affected-test selection.
   - Keep repository-aware context bounded and evidence-driven rather than simply sending
     larger prompts.
   - Proposed completion check: labelled multi-language fixtures demonstrate the new
     parser's selection/impact behavior, including syntax errors, truncation, and
     comparison with the existing selection path.

5. **Checkpointing and resumable tasks**
   - Current foundation: [transaction checkpoints](transactions.md) and
     [retained session summaries](sessions.md). Neither restarts an interrupted model
     loop from a durable execution checkpoint.
   - Persist safe runtime checkpoints across long tasks.
   - Resume after provider failures, process interruption, or rate limiting without
     replaying already completed work unnecessarily.
   - Preserve transaction and verification integrity across resume boundaries.
   - Proposed completion check: interruption tests at each durable phase preserve
     later user edits, avoid replaying completed mutations, invalidate stale checks,
     and require fresh approval when prior authority cannot be safely reused.

6. **Adaptive CLI experience and TUI**
   - Current foundation: [plain/quiet/verbose output and JSON](cli.md), plus
     [terminal presentation](../sable/presentation.py); no rich TUI is claimed.
   - Use a lighter presentation path for casual/read-only interactions that do not need
     the full task lifecycle ceremony.
   - Add a richer terminal interface for task phase, selected context, transactions,
     diffs, capability requests, verification evidence, and trace history.
   - Keep plain/non-interactive output stable for scripts and automation.
   - Proposed completion check: TTY, non-TTY, narrow-terminal, and cancellation tests
     preserve the existing exit/JSON contracts and all applicable policy checks.

### Mid-term — stronger execution, extensibility, and integrations

7. **Stronger optional execution isolation**
   - Current boundary: [native and PRoot backends](execution-security.md) report
     their guarantee levels; neither supplies kernel filesystem/network isolation.
   - Add owner-configured container or OS-backed execution backends where available.
   - Report isolation guarantees explicitly instead of treating containers, PRoot, and
     native execution as equivalent.
   - Preserve native and Termux-friendly modes for environments where stronger isolation
     is unavailable.
   - Proposed completion check: adversarial backend tests demonstrate each advertised
     isolation property; unavailable isolation is reported explicitly and never
     silently downgraded when required by the caller.

8. **Plugin and tool SDK**
   - Current foundation: [built-in schemas](../sable/tool_schemas.py),
     [tool execution](../sable/tools), and [capability policy](../sable/capabilities.py).
     A third-party plugin SDK is future work.
   - Let users add tools without modifying Sable core.
   - Require plugins to declare schemas, capabilities, provenance, and security-relevant
     behavior so runtime policy remains authoritative.
   - Reuse existing tracing, approval, and transaction infrastructure where applicable.
   - Proposed completion check: a sample plugin registers through a documented API;
     undeclared capabilities, malformed schemas, and forged provenance are rejected.

9. **IDE integration**
   - Current interface: [terminal CLI](cli.md). No VS Code extension is shipped.
   - Start with a VS Code integration exposing Sable's existing runtime rather than
     creating a second orchestration path.
   - Surface selected context, diffs, approvals, transactions, verification evidence,
     and traces in an inspectable UI.
   - Proposed completion check: extension actions use the same runtime decisions as
     the CLI; disconnect, cancellation, and stale-approval cases have explicit outcomes.

10. **Local API/server mode**
    - Current foundation: [one-shot JSON output](cli.md) is a final-result process
      contract, not an HTTP/WebSocket service or event-streaming API.
    - Expose the same runtime through a local HTTP/WebSocket interface for IDEs, desktop
      clients, automation, and other front ends.
    - Keep authorization and capability decisions inside the runtime rather than moving
      them into clients.
    - Proposed completion check: define bind-address, authentication, request ownership,
      cancellation, and approval contracts; test unauthorized and cross-task access.

11. **GitHub issue-to-verified-fix workflow**
    - Current foundation: [Git tools](../sable/tools/git.py) and capability-gated
      publication; an issue ingestion and PR workflow is not shipped.
    - Allow a bounded workflow that reads an issue, inspects the repository, proposes and
      applies changes, verifies them, and prepares a reviewable result.
    - Keep commit, push, PR creation, and other publishing actions separately capability
      gated and owner-controlled.
    - Proposed completion check: synthetic issue content cannot grant authority;
      verification evidence and a reviewable diff precede separately approved publication.

12. **Background task queue**
    - Current interface: interactive and one-shot foreground tasks. Build on item 5's
      proposed durable execution contract rather than treating session history as a queue.
    - Add explicit queued, inspectable, resumable tasks instead of relying only on a
      foreground interactive session.
    - Preserve per-task budgets, approvals, transactions, traces, and terminal outcomes.
    - Proposed completion check: restart, cancellation, workspace contention, and
      approval-wait tests prevent duplicated work or authority leaking between tasks.

### Longer-term — optimization and higher-order orchestration

13. **Cost, token, and latency optimization**
    - Current foundation: [RuntimeTask](../sable/runtime.py) records tokens, model
      latency, and task duration; [CLI usage](cli.md) reports token/call counts and
      `/cost` is only an alias. Currency pricing and adaptive cost routing are future work.
    - Track per-task model usage, latency, context volume, and provider cost where pricing
      metadata is available.
   - Add owner-defined monetary budgets and stop conditions alongside the existing
     runtime turn, tool-call, and time limits.
    - Use those signals as routing inputs without hiding model/provider choices from the
      user.
    - Proposed completion check: pricing has provenance and freshness, missing usage
      is reported as unknown, and budget tests bound work without invented cost estimates.

14. **Model reliability scoring**
    - Current foundation: [deterministic and opt-in live evaluations](../evals/README.md).
      Scripted-provider passes do not measure a production model's reliability.
    - Measure tool-call validity, patch success, repair success, latency, and verification
      outcomes per model/provider under Sable's own workloads.
    - Use local evidence to inform routing instead of relying only on generic model
      rankings.
    - Keep evaluation evidence separate from marketing claims and provider benchmarks.
    - Proposed completion check: scores cite model/version, sample size, fixture/task
      mix, repetitions, failures, and skips; routing can explain the evidence it used.

15. **Richer patch and edit strategies**
    - Current foundation: [unified diffs and exact replacements](transactions.md).
      Codex-style patch wrappers already receive actionable rejection/retry guidance
      in [patch regressions](../tests/test_patches.py); automatic dialect conversion,
      model-aware strategy selection, and AST edits remain proposed.
    - Improve selection among unified diffs, exact replacements, structured edits, and
      future AST-aware editing.
    - Add model-aware recovery when a provider repeatedly emits an incompatible patch or
      tool-call dialect.
    - Preserve strict validation and transactional safety rather than accepting ambiguous
      edits for convenience.
    - Proposed completion check: each new strategy passes malformed-input, path,
      context-mismatch, atomic-failure, and rollback tests before model routing uses it.

16. **Project knowledge with provenance**
    - Current foundation: [context snapshots](context-engine.md) and
      [session/task summaries](sessions.md). A reviewable project-fact store with
      provenance/freshness rules is a separate feature.
    - Persist explicit, reviewable project facts such as test commands, generated-file
      rules, architecture conventions, and validated workflow constraints.
    - Track provenance and freshness so historical notes never become an authorization
      source or blindly override current repository evidence.
    - Proposed completion check: changed or deleted sources invalidate affected facts;
      tampered persisted facts cannot change permissions or hide conflicting evidence.

17. **Bounded multi-agent execution**
    - Current foundation: one main agent and fast helper routes that cannot receive
      tools. [ModelRouter](../sable/providers/router.py) does not delegate autonomous tasks.
    - Explore specialized roles such as planner, implementer, reviewer, and verifier only
      where role separation provides measurable value.
    - Give each role bounded context and capabilities under the same runtime policy.
    - Avoid unconstrained agent-to-agent delegation or "agent swarm" behavior that makes
      responsibility and evidence harder to inspect.
    - Proposed completion check: compare a bounded role workflow with the single-agent
      path on declared tasks; test aggregate budgets, write conflicts, cancellation,
      and approval ownership before claiming an orchestration benefit.

## Roadmap principles

Future Sable development should continue to prefer:

- **runtime authority over model authority** — models propose; the runtime enforces;
- **provider independence** — models should be replaceable engines, not architectural
  dependencies;
- **bounded autonomy** — more automation must not imply broader implicit permission;
- **inspectable evidence** — important actions should remain attributable through traces,
  transactions, diffs, and verification results;
- **truthful guarantees** — security and isolation claims must match the active backend;
- **deterministic acceptance** — live-model testing can expand, but must remain separate
  from reproducible deterministic gates;
- **local-first operation** — hosted integrations may grow without making remote services
  mandatory for core workflows.
