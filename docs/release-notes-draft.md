# Sable 2.0.0 release notes draft

> Status: **NOT RELEASED**. This is owner-review material for a proposed 2.0.0
> release. No tag, GitHub Release, or PyPI upload is represented by this document.

Sable 2.0.0 is a local-first agentic coding runtime that puts explicit control around
model-proposed repository work. Groq provides inference; Sable owns bounded tool
execution, workspace-aware context, capability decisions, reversible file edits,
verification, and structured evidence on the local host.

The source already carries version 2.0.0. That makes 2.0.0 the current candidate,
not an automatically approved public version. The owner must confirm it before a tag
or versioned changelog section is created.

## Highlights

### Bounded runtime and tools

- Sequential tool loop: at most one real model-requested action executes per model
  turn, and the next turn observes its result.
- Independent limits for model turns, tool calls, repair cycles, process time,
  output, context, session data, and transaction storage.
- Explicit `plan`, `build`, and `yolo` policy modes. `yolo` makes elevated actions
  requestable; it never removes hard workspace/protected-path boundaries.
- Runtime-owned, exact-action allow-once/session approvals with provenance.

### Repository context and providers

- Bounded repository discovery and symbol/import/test-aware context selection.
- Normalized main/fast provider routing with deterministic local fallback for failed
  fast context compression.
- Groq is the sole concrete production provider in this release.

### Reversible file editing

- File-tool paths are snapshotted before first mutation and finalized with post-state
  fingerprints.
- `/txn`, `/undo --dry-run`, and `/undo` expose bounded, conflict-aware recovery
  without rewriting Git history.
- Later user edits are preserved as conflicts. Arbitrary subprocess side effects are
  outside the transaction guarantee.

### Verification and repair

- Manifest-first QUICK, AFFECTED, and FULL plans execute through the same backend
  and command policy as project tools.
- Structured outcomes distinguish pass, genuine failure, incomplete checks, policy
  blocks, timeouts, and optional skips.
- Genuine failures may receive bounded repair with stable-signature no-progress
  detection and integrity checks against likely test weakening.
- Verified auto-commit requires a passing required verification result.

### Execution controls and observability

- Native and Termux/PRoot backends report `ENFORCED`, `BEST_EFFORT`, and
  `NOT_SUPPORTED` guarantee levels rather than implying container isolation.
- Normal project/build processes use a private HOME, sanitized environment,
  validated cwd, bounded output/time, and shell-disabled-by-default command path.
- Sessions, summaries, JSONL traces, terminal output, and schema-versioned one-shot
  JSON expose bounded redacted task evidence.
- `sable doctor .` provides an offline, read-only readiness check.

### Evaluation and release confidence

- The committed deterministic baseline contains 53 synthetic scenarios spanning
  coding, repair, context, capability policy, prompt injection, integrity attacks,
  transactions, budgets, cancellation, automation, and failure handling.
- Linux CI executes all 53; Windows can report one declared symlink-platform skip.
  Results are controlled-suite conformance, not arbitrary-task or security rates.
- Linux tests Python 3.10–3.13; a separate Windows/Python 3.13 job installs and
  exercises the built wheel.
- Ruff, an 82% statement-coverage floor, runtime dependency audit, Bandit, CodeQL,
  wheel/sdist content checks, Twine, and clean artifact installs are release gates.

## Install for candidate review

No public PyPI installation is claimed. Review from source:

```bash
git clone https://github.com/atrx07/Sable-AI.git
cd Sable-AI
python -m venv .venv
```

Activate the environment, then:

```bash
python -m pip install -e .
sable --version
sable doctor .
```

Or build and install the generated wheel in a clean environment:

```bash
python -m pip install ".[release]"
python -m build
python -m twine check dist/*
python -m pip install dist/sable_ai_agent-2.0.0-py3-none-any.whl
```

Termux users should follow [platform guidance](platforms.md); `install.sh` is
Termux-specific, not a generic host installer.

## Security boundary

Sable provides runtime policy, workspace-confined file tools, approval gates,
environment controls, bounded execution, and prompt-injection hardening. It does not
provide a kernel sandbox, arbitrary child-process filesystem/network isolation,
semantic prompt-injection immunity, or proof of program correctness. Native child
processes retain the Sable OS user's permissions. PRoot is best-effort filesystem
remapping, not a containment boundary. Read [SECURITY.md](../SECURITY.md) before
running unfamiliar project code.

## Known limitations

- Groq is the only concrete production provider.
- Affected-test selection and integrity detection are heuristic.
- Blocking provider-call cancellation depends on host/library interruption points.
- Project-wide type checking is audited but not a blocking gate.
- Termux runtime validation is partly manual; macOS has no dedicated CI.
- Explicit `/keys` entries remain plaintext in the private local config.
- Live-model evaluation is nondeterministic and excluded from ordinary CI.
- Dependency bounds are not a frozen lock, and builds are not claimed bit-for-bit
  reproducible.

## Migration notes

This candidate follows the older ATRX-era one-shot workflow with a runtime-oriented
CLI and architecture. Use a fresh virtual environment when upgrading a checkout.
The bare `sable <path>` form opens an existing workspace; `sable run <task> <path>`
is the stable one-shot form. Existing user projects are not migrated or deleted.

Sable no longer stores GitHub personal access tokens or rewrites remotes with them.
A legacy `~/.sable/git_creds.json` is ignored; configure Git/SSH authentication
normally and remove/rotate obsolete credentials yourself. Older development builds
could persist an environment-supplied Groq key while saving token counts; inspect
your own `~/.sable/config.json` and rotate a key if it was unintentionally stored.

## Before publication

The owner must finalize a dated `## [2.0.0]` changelog section, configure protected
release/PyPI environments and trusted publishing as applicable, establish a private
vulnerability-reporting route, approve the candidate SHA and tag, and deliberately
enable only the intended publication targets. Follow the authoritative
[release procedure and checklist](releasing.md).
