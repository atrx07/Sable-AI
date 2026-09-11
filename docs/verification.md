# Deterministic verification

Sable's verifier is a runtime-owned pipeline. The model may repair a real failure, but it cannot choose whether a check passed, change the verification budget, execute checks outside the command policy, or authorize a blocked action.

## Lifecycle

For a code or verification-configuration change, Sable:

1. discovers local project manifests and toolchains;
2. creates a typed, deterministic verification plan;
3. selects checks for the requested scope;
4. runs checks sequentially through the M4 process policy;
5. classifies failures and stores bounded, redacted evidence;
6. optionally asks the main agent for a bounded repair;
7. after a repair, runs `QUICK`, reruns the checks that failed, and then rediscovers the requested final scope;
8. permits auto-commit only after a passing required verification outcome.

The default scope is `AFFECTED`. Configure it interactively with:

```text
/verify quick
/verify affected
/verify full
/verify scope affected
```

`/verify on|off` remains available. `/run <command>` is a complete, single-check override for the current CLI session, but still passes through the same command and capability policy.

## Scopes

| Scope | Intent |
|---|---|
| `QUICK` | Cheap syntax, compile, or equivalent smoke checks. |
| `AFFECTED` | Quick checks plus tests/checks related to changed files. This is the default. |
| `FULL` | All discovered required checks within the bounded project roots and budgets. |

Changes to CI workflows, manifests, lock files, build files, test configuration, or verification configuration escalate `AFFECTED` to `FULL`. The plan records both requested and effective scopes and the reason for escalation.

Affected selection uses bounded repository context, Python imports, test naming and location, direct Node test-runner proximity, and conservative fan-out for central modules. It is an optimization, not proof that unrelated tests cannot fail; use `FULL` when that assurance matters.

## Discovery and adapters

Discovery is manifest-first and does not install dependencies. Current adapters cover:

- Python (`compileall`, unittest/pytest, and configured lint/type tools)
- Node/TypeScript (safe configured scripts and direct local runners)
- Rust (`cargo` with offline behavior)
- Go (`go test` with readonly module behavior)
- Maven and Gradle (preferring repository wrappers and offline behavior)

Nested project roots are supported. Manifest and wrapper symlinks must resolve inside the workspace. Unsafe package scripts are ignored rather than executed. Missing tools or dependencies are reported as unavailable; Sable does not silently publish a pass and does not auto-install them.

## Outcomes

Checks have explicit statuses such as `PASS`, `FAIL`, `TIMEOUT`, `ERROR`, `BLOCKED`, and skipped variants. Runs aggregate those into:

- `PASS` or `PASS_WITH_OPTIONAL_SKIPS` — all required verification completed successfully;
- `FAIL` — a required check produced a genuine validation failure;
- `INCOMPLETE` — required evidence could not be obtained, a budget was exhausted, or a required tool was unavailable;
- `BLOCKED` — runtime policy prevented a required check;
- `SKIPPED` — there were no applicable checks.

Only genuine `FAIL` outcomes trigger model repair. Missing tools, policy blocks, and timeouts are not treated as code defects. Code/config changes with no runnable required checks become `INCOMPLETE` and cannot be auto-committed. Documentation-only changes may remain skipped.

## Budgets and repair

Plans bound the number of checks, per-check timeout, total wall time, and repair cycles. Checks run in deterministic cost/category order and use fail-fast cancellation where configured.

Repair prompts contain bounded structured diagnostics: check identity, classification, stable failure signature, selection reason, and redacted diagnostic text. After each repair Sable performs a cheap quick pass, reruns prior failed checks, and performs the final requested verification. Repeating the same non-empty set of failure signatures terminates as `REPAIR_NO_PROGRESS` rather than spending the remaining cycles.

## Test-integrity heuristics

Before agent edits, Sable records a bounded baseline of existing test files and relevant `package.json` scripts. After verifier-driven repair it flags likely validation weakening, including:

- deleting an existing test without explicit user intent;
- adding a blanket skip/xfail;
- removing substantial assertions or test code;
- replacing assertions with `pass`;
- disabling a configured verification script with a no-op.

High-confidence weakening blocks verification and auto-commit. Lower-confidence changes are warnings and remain visible in evidence. Explicit user requests to maintain or remove tests reduce selected severities. These are conservative lexical heuristics, not semantic proof that a test remains meaningful.

## Evidence and observability

Every structured run has a plan ID and evidence ID. Evidence records effective scope, overall status, check metadata, classifications, stable signatures, duration, budget state, repair count, and integrity warnings. Command output and diagnostics are secret-redacted and length-bounded; volatile approval IDs are excluded.

Compact evidence links and summaries are persisted with runtime tasks, sessions, and reversible transactions. Detailed lifecycle events cover plan creation, scope escalation, check start/result, failure classification, budget exhaustion, repair requests, no-progress termination, integrity warnings, and completion.

## Security and limitations

Verification uses the same private HOME, sanitized environment, workspace cwd validation, process timeout handling, and capability policy as other project commands. Dedicated Git credentials are not inherited by verification subprocesses.

Verification is not an OS sandbox. A test or build can still access resources available to the Sable process on native hosts, and PRoot is only best-effort path remapping. Discovery cannot identify every custom toolchain, affected selection cannot prove complete test coverage, classifiers are diagnostic heuristics, and integrity checks cannot prove semantic test quality. Use a separately configured container or VM for hostile repositories.
