# Sable evaluations

Sable's M7 evaluation suite exercises completed product paths across the agent loop, tools, capability policy, context selection, verification, transactions, sessions, automation output, and cancellation. It complements unit tests; it is not a general software-engineering benchmark or a claim that Sable is vulnerability-free.

## Architecture

Each `EvalScenario` is loaded from a versioned JSON catalog. `FixtureManager` copies a small canonical repository into a fresh temporary workspace, records its file fingerprints, and removes the copy after the scenario. `ScriptedProvider` returns a finite declared sequence without making network requests. `SystemScenarioExecutor` drives real Sable components, and the assertion engine checks outcomes, changed and forbidden files, events, capabilities, verification state, exit codes, and bounded resource use.

`EvalResult` and the aggregate report use schema version 1. A scenario has one of these dispositions:

- `PASS` or `FAIL` after execution
- `SKIPPED_PLATFORM` when the host genuinely cannot provide a required primitive
- another explicit `SKIPPED_*` state for unavailable, disabled, or unrequested execution

Skipped scenarios are never counted as passes. Reports list failures and skips separately.

## Deterministic suite

From the repository root, run the complete acceptance suite and compare it with the committed baseline:

```bash
python -m sable.evals \
  --baseline evals/baselines/m7-deterministic.json \
  --output evals/reports/generated/local
```

The command writes `eval-results.json` and `eval-report.md` and exits nonzero for a scenario failure or baseline regression. Generated reports are ignored by Git. The deterministic catalogs contain 53 focused scenarios; on a Windows host without symlink permission, `security.symlink_escape` is reported as `SKIPPED_PLATFORM`, so a healthy report is 52/52 completed scenarios plus one skip. A host that can create symlinks executes all 53.

Useful unbaselined subsets are:

```bash
python -m sable.evals --scenario coding.simple_bug
python -m sable.evals --category SECURITY
python -m sable.evals --category TRANSACTION --output evals/reports/generated/transactions
```

Baseline comparison intentionally requires an unfiltered deterministic run with `--repeat 1`. Deterministic execution uses no provider key, no public network, the scripted provider, local synthetic Git remotes, and temporary fixture/state directories. CI runs this same command in a separate job after the Python 3.10–3.13 test matrix.

## Optional live suite

Live evaluation is opt-in and is never run by normal tests or CI:

```bash
python -m sable.evals --live --repeat 3 --output evals/reports/generated/live-local
python -m sable.evals --live --scenario live.simple_bug --repeat 5
```

`--live` constructs the configured Groq client and can consume provider quota. The report records mode, provider, model, and temperature. Each repetition gets a newly materialized fixture; results are aggregated without collapsing failures. Do not attach the deterministic baseline to a live or repeated run.

## Fixture and scenario format

Canonical fixture repositories live under `evals/fixtures/`; JSON catalogs live under `evals/scenarios/`. Required scenario fields are:

```json
{
  "scenario_id": "example.bounded_property",
  "title": "Human-readable title",
  "category": "FAILURE_HANDLING",
  "description": "The specific product property this proves.",
  "fixture": "framework_smoke",
  "task_prompt": "Inspect the fixture safely.",
  "expected_outcome": "TASK_PASS"
}
```

Optional fields declare scripted model responses, machine assertions, expected/forbidden changes, expected capabilities, labeled context files, verification scope and command, Git initialization, synthetic user writes, undo behavior, specialized deterministic fault states, budgets, and tags. Paths must be confined relative paths. Unknown enums, duplicate IDs, malformed scripts, unsafe fixture paths, and non-positive budgets fail during loading.

To add a scenario:

1. Add or reuse a minimal fixture that contains only synthetic data.
2. State one concrete property in `description`.
3. Script finite provider responses; never rely on a live provider for deterministic coverage.
4. Add assertions over externally meaningful state or runtime evidence, not implementation trivia.
5. Tag CI-safe deterministic cases with `ci`.
6. Run the scenario alone, then the full deterministic suite.
7. Add its ID to the canonical baseline only after the expectation is justified and the full gate is green.

Do not add fixture-name special cases to production Sable, weaken an expectation after a real failure, silently skip a case, or commit generated reports.

## Metrics

Every rate records numerator, denominator, and percentage:

- `scenario_pass_rate`: passing completed scenarios / completed scenarios; skips are excluded and separately counted.
- `task_success_rate`: passing scenarios whose expected outcome is `TASK_PASS` / completed `TASK_PASS` scenarios.
- `verified_success_rate`: passing and verified functional scenarios / functional scenarios that ran verification.
- `security_scenario_pass_rate`: passing security, prompt-injection, and capability cases / completed cases in those categories.
- `safe_refusal_rate`: passing expected safe-refusal or capability-denial cases / completed refusal cases.
- `rollback_correctness_rate`: passing expected rollback-success/conflict cases / completed rollback cases.
- `integrity_detection_rate`: passing integrity cases / completed integrity cases.
- `repair_success_rate`: passing repair cases / completed repair cases. Some repair cases intentionally expect bounded failure, so this measures conformance to the expected repair outcome rather than only successful code changes.

`fixture_context_recall` and `fixture_context_precision` are distributions over scenarios with fixture-defined relevance labels. Efficiency distributions cover model calls, tool calls, duration, and token usage. Timing is diagnostic only and is not a fragile pass threshold.

## Baseline

[`evals/baselines/m7-deterministic.json`](baselines/m7-deterministic.json) uses baseline schema version 1 and eval schema version 1. It fixes the scenario ID set, permits only the symlink case as a platform skip (at most one), and enforces minimum independent rate/context metrics. It excludes timestamps, commit IDs, host strings, event IDs, temporary paths, and durations, so the acceptance contract is stable across runs and supported platforms. Generated reports retain those useful observational fields and therefore are semantically reproducible, not byte-for-byte snapshots.

Changing the baseline is a reviewable contract change. A missing scenario, unexpected scenario, hidden/non-platform skip, failed scenario, schema mismatch, or metric below its threshold fails comparison.

## Security constraints and limitations

- Fixtures contain synthetic data only. The redaction scenario uses a deliberately fake token-shaped value.
- Deterministic provider responses are local declarations and never call Groq.
- Network and package attempts are denied before execution. Git-publish policy uses a temporary local bare remote.
- Temporary workspaces and state directories are deleted after each scenario.
- Security cases validate Sable's documented M4 boundaries; they do not provide arbitrary-code, kernel, filesystem-namespace, process, or network containment.
- Fixture tasks cover representative repositories, not the full software-engineering world.
- Live model results are nondeterministic and vary by provider, model, prompt, and service conditions.
- The sample is too small for broad statistical claims, and context relevance labels are fixture-defined.
- Passing evaluations do not prove the absence of defects or vulnerabilities.
