# Sable v2 development roadmap

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

## Possible post-v2 work

These are possibilities, not current features or commitments:

- incrementally resolve project-wide typing debt and adopt a useful blocking checker;
- add more production providers or local-model integrations;
- offer stronger owner-configured container/OS isolation for hostile code;
- broaden real Termux/macOS validation and Windows test coverage;
- expand live-model evaluation while keeping deterministic acceptance separate;
- improve streaming, language-aware parsing, IDE integration, or an API/server mode.

Future work must preserve the existing runtime policy, transaction, verification,
evaluation, and release guarantees or explicitly version any incompatible contract.
