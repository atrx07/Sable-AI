# Sable development roadmap

The original v2 roadmap is complete. These milestones describe the engineering
history; they are not separate editions that users must install.

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

Sable's source version is 2.0.0 and the repository is release-candidate ready, but a
version number is not proof of publication. The owner must still approve the public
version, configure external release protections and trusted publishing as desired,
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

### Near-term — provider independence and runtime intelligence

1. **Multi-provider support**
   - Generalize the provider layer so Groq is one backend rather than a product-level
     dependency.
   - Add first-class support for additional hosted providers such as OpenAI, Anthropic,
     and Gemini where their APIs and tool contracts fit Sable's runtime model.
   - Support OpenAI-compatible endpoints so local gateways and compatible providers can
     be integrated without provider-specific logic leaking into the runtime.
   - Keep credentials, rate limits, model metadata, and provider errors isolated behind
     the provider abstraction.

2. **Local and offline model execution**
   - Add integrations for local runtimes such as Ollama or compatible local endpoints.
   - Allow an explicitly offline Sable workflow that does not require a hosted inference
     provider.
   - Preserve the same capability, transaction, trace, and verification controls for
     local models as for hosted models.

3. **Role-aware and adaptive model routing**
   - Move beyond a simple main/fast split toward explicit runtime roles such as planner,
     executor, repair, context, and summarization.
   - Route using model capabilities, tool-call reliability, context limits, latency,
     provider health, and owner-defined cost budgets.
   - Add bounded provider fallback for outages or rate limits without silently weakening
     task policy.

4. **Semantic code intelligence**
   - Add language-aware parsing, likely through Tree-sitter or equivalent structured
     analysis.
   - Improve symbol lookup, import/call relationships, semantic chunking, changed-symbol
     impact analysis, and affected-test selection.
   - Keep repository-aware context bounded and evidence-driven rather than simply sending
     larger prompts.

5. **Checkpointing and resumable tasks**
   - Persist safe runtime checkpoints across long tasks.
   - Resume after provider failures, process interruption, or rate limiting without
     replaying already completed work unnecessarily.
   - Preserve transaction and verification integrity across resume boundaries.

6. **Adaptive CLI experience and TUI**
   - Use a lighter presentation path for casual/read-only interactions that do not need
     the full task lifecycle ceremony.
   - Add a richer terminal interface for task phase, selected context, transactions,
     diffs, capability requests, verification evidence, and trace history.
   - Keep plain/non-interactive output stable for scripts and automation.

### Mid-term — stronger execution, extensibility, and integrations

7. **Stronger optional execution isolation**
   - Add owner-configured container or OS-backed execution backends where available.
   - Report isolation guarantees explicitly instead of treating containers, PRoot, and
     native execution as equivalent.
   - Preserve native and Termux-friendly modes for environments where stronger isolation
     is unavailable.

8. **Plugin and tool SDK**
   - Let users add tools without modifying Sable core.
   - Require plugins to declare schemas, capabilities, provenance, and security-relevant
     behavior so runtime policy remains authoritative.
   - Reuse existing tracing, approval, and transaction infrastructure where applicable.

9. **IDE integration**
   - Start with a VS Code integration exposing Sable's existing runtime rather than
     creating a second orchestration path.
   - Surface selected context, diffs, approvals, transactions, verification evidence,
     and traces in an inspectable UI.

10. **Local API/server mode**
    - Expose the same runtime through a local HTTP/WebSocket interface for IDEs, desktop
      clients, automation, and other front ends.
    - Keep authorization and capability decisions inside the runtime rather than moving
      them into clients.

11. **GitHub issue-to-verified-fix workflow**
    - Allow a bounded workflow that reads an issue, inspects the repository, proposes and
      applies changes, verifies them, and prepares a reviewable result.
    - Keep commit, push, PR creation, and other publishing actions separately capability
      gated and owner-controlled.

12. **Background task queue**
    - Add explicit queued, inspectable, resumable tasks instead of relying only on a
      foreground interactive session.
    - Preserve per-task budgets, approvals, transactions, traces, and terminal outcomes.

### Longer-term — optimization and higher-order orchestration

13. **Cost, token, and latency optimization**
    - Track per-task model usage, latency, context volume, and provider cost where pricing
      metadata is available.
    - Add owner-defined task budgets and stop conditions.
    - Use those signals as routing inputs without hiding model/provider choices from the
      user.

14. **Model reliability scoring**
    - Measure tool-call validity, patch success, repair success, latency, and verification
      outcomes per model/provider under Sable's own workloads.
    - Use local evidence to inform routing instead of relying only on generic model
      rankings.
    - Keep evaluation evidence separate from marketing claims and provider benchmarks.

15. **Richer patch and edit strategies**
    - Improve selection among unified diffs, exact replacements, structured edits, and
      future AST-aware editing.
    - Add model-aware recovery when a provider repeatedly emits an incompatible patch or
      tool-call dialect.
    - Preserve strict validation and transactional safety rather than accepting ambiguous
      edits for convenience.

16. **Project knowledge with provenance**
    - Persist explicit, reviewable project facts such as test commands, generated-file
      rules, architecture conventions, and validated workflow constraints.
    - Track provenance and freshness so historical notes never become an authorization
      source or blindly override current repository evidence.

17. **Bounded multi-agent execution**
    - Explore specialized roles such as planner, implementer, reviewer, and verifier only
      where role separation provides measurable value.
    - Give each role bounded context and capabilities under the same runtime policy.
    - Avoid unconstrained agent-to-agent delegation or "agent swarm" behavior that makes
      responsibility and evidence harder to inspect.

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
