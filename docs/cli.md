# Sable CLI

Sable can open an existing workspace interactively, run one task for automation, or inspect local readiness without changing the project.

## Install and start

```bash
git clone https://github.com/atrx07/Sable-AI.git
cd Sable-AI
bash install.sh
sable .
```

The main command forms are:

```text
sable                         legacy project-slot shell
sable .                       interactive shell in the current directory
sable chat .                  explicit interactive form
sable chat /path/to/project   interactive shell in an existing directory
sable run "Fix the parser" .  run exactly one task, then exit
sable doctor .                offline, read-only readiness checks
sable --version
sable --help
```

An explicitly targeted workspace is resolved to its existing directory and used in place. Sable does not copy it into the configured project-slot directory. Protected paths and nonexistent or non-directory targets are refused before the CLI runtime is constructed.

## Invocation options

The common options can appear before or after a subcommand:

```text
--mode plan|build|yolo
--verify quick|affected|full|off
--plain
--no-color
--quiet
--verbose
--json                         sable run only
```

Command-line mode and verification overrides apply to that invocation. Interactive `/mode` and `/verify` commands update the existing configuration. `/config` shows effective models, budgets, verification settings, execution backend, Git automation, and project storage. Groq keys are managed separately with `/keys` and are always masked in display output.

`plan`, `build`, and `yolo` retain the runtime capability policy described in [execution-security.md](execution-security.md). There is no approve-all flag. With verification off, a completed build exits successfully but is explicitly reported as `UNVERIFIED`.

## Interactive shell

Common inspection and control commands are:

```text
/help
/status
/diff [path]
/usage                       /cost is an alias
/doctor
/mode plan|build|yolo
/verify on|off|quick|affected|full
/verify scope quick|affected|full
/run <verification command>
/txn [list|show <transaction-id>]
/undo [transaction-id] [--dry-run]
/session [list|show <session-id>]
/trace [task-id]
/sandbox
/clear
/exit
```

`/usage` reports main-model calls, fast-model calls, and token counts. It does not estimate currency cost because Sable has no authoritative pricing source. `/sandbox` reports the actual backend guarantees; native and PRoot execution are not described as kernel isolation.

Sable does not add a persistent terminal command-history file. Session records retain bounded task summaries, runtime metadata, and redacted trace events as documented in [sessions.md](sessions.md); they are not a forever-growing raw prompt history.

## Capability approvals

When the runtime requests an elevated action, the prompt displays runtime-owned capability, action, source, risk, tool, and reason fields. The accepted decisions are:

```text
a / allow / once    allow once
s / session         allow that exact action during this session
d / deny / Enter    deny
```

Invalid input asks again and never approves. EOF and Ctrl+C deny. In a one-shot run whose stdin is not a TTY, interactive approvals are disabled so the command cannot hang or grant an implicit capability.

## Plain, color, and non-TTY output

`--plain` forces stable, line-oriented text without ANSI or cursor rewriting. `--no-color`, the `NO_COLOR` environment variable, `TERM=dumb`, and non-TTY stdout also disable color. Rendering is width-aware and remains usable in narrow Termux windows; presentation may wrap or bound details, while persisted runtime evidence is unchanged.

`--quiet` emits only the final status label. `--verbose` adds bounded context and failure details, but it does not disable secret redaction.

## JSON automation output

Use JSON only with a one-shot command:

```bash
sable run "Fix the parser bug" . --json
```

Stdout contains exactly one compact JSON document followed by a newline. Live phase, tool, verification, warning, and approval messages go to stderr. Schema version 1 has these always-present fields:

```json
{
  "schema_version": 1,
  "status": "completed",
  "result": "pass",
  "verified": true,
  "verification_enabled": true,
  "verification_status": "PASS",
  "task_id": "task-1",
  "session_id": "session-1",
  "transaction_id": "txn-1",
  "changed_files": ["sable/parser.py"],
  "commit": "abc123",
  "usage": {
    "main_model_calls": 2,
    "fast_model_calls": 1,
    "input_tokens": 120,
    "output_tokens": 40,
    "total_tokens": 160
  },
  "duration_ms": 1250,
  "exit_reason": "VERIFICATION_PASSED",
  "exit_code": 0,
  "summary": "Fixed the parser bug.",
  "verification_checks": [
    {"name": "Python syntax", "status": "PASS", "classification": "NONE"}
  ]
}
```

`status` is the automation lifecycle (`completed`, `failed`, `blocked`, or `cancelled`); `result` preserves Sable's more specific internal final state. `verified` is true only when verification is enabled and the final status is `PASS` or `PASS_WITH_OPTIONAL_SKIPS`. Optional fields include `undo_available`, bounded `rollback` metadata, and redacted `warnings`. Raw tool output is intentionally absent.

Schema version 1 is a final-result contract, not an event-streaming protocol. Consumers should check both `schema_version` and the process exit code.

## Exit codes

| Code | Meaning |
|---:|---|
| `0` | Task completed (`VERIFIED`, plan-only, or explicitly `UNVERIFIED`) |
| `2` | Command usage or critical configuration error |
| `10` | Verification failed, was incomplete/blocked, or repair made no progress |
| `20` | Capability/policy denial or runtime budget block |
| `21` | Selected execution backend unavailable or invalid |
| `30` | Provider/model failure |
| `70` | Internal or otherwise unclassified runtime failure |
| `130` | User cancellation |

These values are the stable CLI process contract. JSON output repeats the selected value in `exit_code`.

## Doctor

`sable doctor [path]` and interactive `/doctor` perform the same deterministic local audit. Doctor is offline, read-only, and never installs tools. It reports:

- Sable and Python versions plus Python 3.10–3.13 CI support;
- configuration, session, and transaction storage accessibility;
- workspace read/write state and protected-path policy;
- configured provider/models and API-key presence as a boolean only;
- Git executable, repository, branch, dirty state, and origin presence;
- selected backend, availability, guarantee summary, Termux, and PRoot state;
- verification configuration, detected toolchains, and tool availability.

Warnings and unavailable optional tools do not fail doctor. A missing required provider configuration, inaccessible required storage/workspace, invalid backend, or explicitly selected unavailable PRoot backend returns nonzero. Doctor does not contact Groq, print credential values, inspect credential file contents, or scan arbitrary home-directory files.

## Cancellation and limitations

Ctrl+C at the idle prompt exits cleanly. During a task it records `USER_ABORT`, stops new agent/verification/repair work, prevents auto-commit, and leaves Sable-created partial edits finalized as failed and recoverable with `/undo` where snapshots exist. A running native subprocess uses the execution backend's descendant-cleanup path.

Blocking provider calls can only be interrupted where the host/provider library permits; cancellation is otherwise observed at bounded runtime transitions. Sable does not stream model tokens in M6. Terminal Unicode and width behavior vary by host. PRoot remains best-effort and does not provide kernel isolation. See [execution-security.md](execution-security.md), [transactions.md](transactions.md), and [verification.md](verification.md) for the authoritative boundaries.
