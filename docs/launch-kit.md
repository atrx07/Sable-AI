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

Sable started from a simple question: what has to surround a coding model before its
repository work becomes inspectable and recoverable?

The result is a local-first runtime rather than a thin LLM-to-shell wrapper. The
model can request one real tool action per turn; Sable owns workspace path checks,
budgets, capability decisions, exact-action approvals, transaction snapshots,
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
rollback, budgets, cancellation, and JSON automation. Linux CI runs all scenarios;
Windows packaging and installed-CLI behavior have a separate smoke check.

The security boundary is deliberately explicit. Sable confines its own file tools,
sanitizes normal child environments, uses private process HOME directories, and
capability-gates known high-risk actions. Native subprocesses still have the OS
user's permissions, and PRoot is best-effort remapping—not a kernel sandbox or
network namespace.

The repository includes architecture diagrams, reproducible demos, evaluation
methodology, quality gates, and release-ready automation. At draft time no public
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

I’ve finished Sable v2, a project I used to explore a less glamorous—but more
important—part of agentic coding: the runtime around the model.

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

The biggest lesson was that trustworthy agent behavior comes less from a stronger
prompt and more from explicit state machines, policy boundaries, recoverability, and
machine-checkable evidence.

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
   [demo.md](demo.md), then show its generated report and 5/5 assertion outcome.
8. **Close (15 seconds):** limitations, source link, and contribution guide.

Suggested stills: doctor, backend guarantees, verified result, capability prompt,
undo dry run, and deterministic summary. Inspect all frames for credentials, local
usernames/paths, private remotes, session IDs, and unrelated terminal history.

## Measured facts safe to cite after final refresh

- Current source/candidate version: 2.0.0; at draft time no tag/release/PyPI upload.
- Committed deterministic catalog: 53 scenarios; Linux runs 53/53, while Windows may
  use one declared symlink-platform skip and complete 52/52.
- Controlled-suite denominators on the latest local Windows baseline run: functional
  tasks 19/19, verified cases 14/14, security/adversarial cases 15/15, safe refusals
  13/13, rollback correctness 7/7, repairs 5/5, integrity detection 4/4.
- CI versions: Linux Python 3.10–3.13; Windows installed-wheel smoke on Python 3.13.
- Quality threshold: at least 82% production-package statement coverage.
- Artifact gates: wheel and sdist build, Twine metadata, content inspection, isolated
  installs, CLI version/help/doctor, release notes, and SHA-256 verification.

Always include denominators for evaluation rates and qualify them as deterministic
fixture-suite results. Do not convert these facts into claims of arbitrary task
success, vulnerability freedom, adoption, downloads, or comparison wins.
