# Sable launch kit

> Draft copy only. Repository metadata, social posts, tags, releases, and package
> publication require separate owner action. Refresh measured facts against the
> final candidate before posting.

## One-line description

Sable is a local-first agentic coding runtime with bounded tools, transactional
editing, capability approvals, deterministic verification, and adversarial evaluation.

## Short description

Sable wraps model-proposed coding work in an inspectable local runtime. It combines
workspace-confined file tools, conflict-aware undo, scoped human approvals,
repository-aware context, structured verification, and deterministic fixture-based
evaluation while stating its OS-isolation limits explicitly.

## Proposed GitHub metadata

Repository description:

> Local-first agentic coding runtime with bounded tools, transactional editing,
> capability approvals, deterministic verification, and adversarial evaluation.

Suggested topics:

```text
agentic-ai
coding-agent
ai-agent
llm-agent
developer-tools
automation
python
termux
tool-calling
code-generation
```

These are proposals. This draft does not change the repository description or topics.

## Short launch announcement

I built Sable, a local-first agentic coding runtime for inspecting and changing real
repositories with explicit controls around model actions. It combines bounded tools,
transactional editing and conflict-aware undo, scoped capability approvals,
deterministic verification, structured traces, and a 53-scenario synthetic evaluation
suite. The repository documents both what Sable enforces and what it does not:
native/PRoot execution is not kernel isolation. Source and demos:
https://github.com/atrx07/Sable-AI

## Longer technical announcement

Sable makes model-proposed repository work inspectable and recoverable through a
local runtime. Groq supplies hosted inference and receives selected repository
context. One real model-requested tool action can execute per turn; Sable owns
workspace path checks, budgets, capability decisions, exact-action approvals, transaction snapshots,
verification status, and optional Git automation. Repository files, model output,
and process/test output are untrusted data and cannot grant themselves authority.

File-tool changes are recorded in bounded transactions with post-state fingerprints,
so `/undo --dry-run` can distinguish a safe restore from a later user edit. The
verification engine discovers project checks, plans QUICK/AFFECTED/FULL scopes,
classifies failures, and permits bounded repair only for genuine failures. Missing
tools, timeouts, or policy blocks stay incomplete—they are not painted green.

For repeatable evidence, Sable has 53 deterministic synthetic scenarios that drive
the real runtime using finite scripted provider responses. They cover coding and
repair, context, capability policy, prompt injection, test-integrity attacks,
rollback, budgets, cancellation, and JSON automation. Linux CI is configured to
run all scenarios; Windows packaging and installed-CLI behavior have a separate
smoke check.

The security boundary is deliberately explicit. Sable confines its own file tools,
sanitizes normal child environments, uses private process HOME directories, and
capability-gates known high-risk actions. Native subprocesses still have the OS
user's permissions, and PRoot is best-effort remapping—not a kernel sandbox or
network namespace.

The repository includes architecture diagrams, reproducible demos, evaluation
methodology, quality gates, and release-validation automation. At draft time no public
package release is being claimed; use the documented source installation until an
owner-approved release exists: https://github.com/atrx07/Sable-AI

## LinkedIn project entry

Designed and built Sable, a local-first agentic coding runtime with bounded tools,
transactional editing and conflict-aware undo, capability approvals, deterministic
verification and repair, repository-aware context, sessions/tracing, native and
Termux/PRoot execution backends, one-shot JSON automation, and adversarial synthetic
evaluation. Established Python 3.10–3.13 CI, Windows installed-wheel validation, an
82% coverage floor, runtime dependency/static security scanning, and safely gated
release automation. Documented the limits: arbitrary subprocess isolation and proof
of correctness are not claimed.

## Suggested LinkedIn launch post

Sable is a Python CLI project focused on the runtime around a coding model.

A coding model can suggest an edit quickly. The harder engineering questions are:
Who decides what may execute? What happens to existing user work? What evidence
counts as verification? Can a failure be distinguished from a missing tool? Can an
edit be undone without erasing a later human change?

Sable answers those questions with a local control plane:

- one real model-requested tool action per turn, with bounded budgets;
- workspace-confined file tools and runtime-owned capability approvals;
- transactional snapshots with fingerprint-based conflict detection;
- repository-aware context and QUICK/AFFECTED/FULL verification plans;
- bounded repair with repeated-failure and test-weakening safeguards;
- structured sessions, traces, terminal output, and one-shot JSON results;
- 53 deterministic synthetic scenarios through the real product path.

The limits matter just as much. Native subprocesses retain the OS user's permissions.
PRoot is best-effort path remapping, not kernel isolation. Verification is evidence
from configured checks, not a proof of global correctness, and the suite is not a
cross-product benchmark.

Current inference uses Groq; local tools and evidence do not mean offline model
execution. Multi-provider support, local models, and richer integrations are
proposed in the roadmap, not current features.

Architecture, demos, evaluation methodology, and source:
https://github.com/atrx07/Sable-AI

## Demo recording outline

Use a clean synthetic or disposable repository. Record real output without cuts that
change apparent outcomes.

1. **Opening (15 seconds):** repository README and one-line positioning.
2. **Readiness (20 seconds):** `sable doctor .`; explain offline/read-only behavior.
3. **Controls (30 seconds):** `sable .`, `/status`, `/sandbox`, and actual guarantee
   levels. State that native/PRoot is not kernel isolation.
4. **Task (60 seconds):** a small repair with context selection, changed files, and a
   real verification PASS. Do not expose a key or private project.
5. **Approval (30 seconds):** a genuine elevated request and deny/allow-once/session
   choices; explain exact-action scope.
6. **Recovery (45 seconds):** `/txn`, `/undo --dry-run`, then safe undo or a real
   fingerprint conflict in a disposable file.
7. **Evidence (30 seconds):** run the five-scenario command in
   [demo.md](demo.md), then show its generated report and actual assertion outcomes.
8. **Close (15 seconds):** limitations, source link, and contribution guide.

Suggested stills: doctor, backend guarantees, verified result, capability prompt,
undo dry run, and deterministic summary. Inspect all frames for credentials, local
usernames/paths, private remotes, session IDs, and unrelated terminal history.

## Source-linked facts and measurements to refresh

- Current source/candidate version: 2.0.0, from
  [the version literal](../sable/_version.py). Check external publication status
  separately before describing an installable release.
- Committed deterministic catalog: 53 scenarios, with at most one declared
  symlink-platform skip, from [the baseline](../evals/baselines/m7-deterministic.json).
  Obtain actual passes, failures, skips, and metric denominators from a recorded run.
- Configured CI versions: Linux Python 3.10–3.13; Windows installed-wheel smoke on
  Python 3.13, from [ci.yml](../.github/workflows/ci.yml).
- Configured quality threshold: 82% production-package statement coverage, from
  [pyproject.toml](../pyproject.toml). Cite measured coverage only with its run evidence.
- Configured artifact gates: wheel/sdist build, Twine metadata, content inspection,
  isolated installs, CLI version/help/doctor, release notes, and SHA-256 verification,
  from [quality](../scripts/quality.py), [artifact checks](../scripts/release_check.py),
  and [release validation](../.github/workflows/release.yml).

Always include denominators for evaluation rates and qualify them as deterministic
fixture-suite results. Do not convert these facts into claims of arbitrary task
success, vulnerability freedom, adoption, downloads, or comparison wins.
Use the [evidence map](evidence.md) for technical claims and the
[roadmap](roadmap.md) for explicitly proposed work. Future local-model, multi-provider,
IDE, server, and multi-agent items must not appear as current product capabilities.
