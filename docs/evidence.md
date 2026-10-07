# Implementation evidence

Use this map to check current capability claims before writing release notes or
portfolio copy. Source shows the implemented path; tests and scenarios show the
declared behavior under their inputs. Their presence does not establish that a
particular checkout passed: inspect a run report or exact-commit CI result.

## Current capabilities

| Capability | Implementation | Regression / acceptance evidence | Scope |
|---|---|---|---|
| Sequential agent tools and budgets | [main_agent.py](../sable/main_agent.py), [runtime.py](../sable/runtime.py) | [test_agent_loop.py](../tests/test_agent_loop.py), [resilience scenarios](../evals/scenarios/m7.5-resilience.json) | One real model-requested tool action per turn; model/tool limits are runtime controls |
| Capability decisions and approvals | [capabilities.py](../sable/capabilities.py), [executor.py](../sable/tools/executor.py) | [test_capabilities.py](../tests/test_capabilities.py), [adversarial scenarios](../evals/scenarios/m7.3-adversarial.json) | Exact-action grants; plan mode cannot be elevated; persisted grants are not restored |
| Workspace file tools and execution controls | [security.py](../sable/security.py), [file writes](../sable/tools/files_write.py), [backends](../sable/execution) | [test_security.py](../tests/test_security.py), [test_m4_security.py](../tests/test_m4_security.py), [test_execution_backends.py](../tests/test_execution_backends.py) | File-tool confinement; native/PRoot subprocess limits are separate and documented in [SECURITY.md](../SECURITY.md) |
| Transactions and conflict-aware undo | [transactions.py](../sable/transactions.py) | [test_transactions.py](../tests/test_transactions.py), [system scenarios](../evals/scenarios/m7.2-system.json) | File-tool snapshots and checkpoints; later edits cause conflicts; arbitrary process side effects are outside coverage |
| Unified diffs and format recovery | [patches.py](../sable/patches.py), [tool_schemas.py](../sable/tool_schemas.py) | [test_patches.py](../tests/test_patches.py) | Validated conventional diffs; unsupported Codex wrappers get retry guidance, not automatic conversion |
| Repository context | [context engine](../sable/context/engine.py), [context models](../sable/context/models.py) | [test_context.py](../tests/test_context.py), [system scenarios](../evals/scenarios/m7.2-system.json) | Python AST symbols/imports, bounded ranking, cache invalidation, heuristic test relationships |
| Provider abstraction and main/fast routing | [base.py](../sable/providers/base.py), [router.py](../sable/providers/router.py), [groq.py](../sable/providers/groq.py) | [test_providers.py](../tests/test_providers.py), [test_groq_client.py](../tests/test_groq_client.py) | Groq is the production adapter; fast helpers have no tools and can fall back to deterministic text |
| Verification and bounded repair | [verification package](../sable/verification), [orchestrator.py](../sable/orchestrator.py) | [test_verification_orchestration.py](../tests/test_verification_orchestration.py), [test_verification_integrity.py](../tests/test_verification_integrity.py), [system scenarios](../evals/scenarios/m7.2-system.json) | Required failure, missing evidence, and policy blocks remain distinct; selection/integrity are heuristics |
| Session history, traces, and usage | [session manager](../sable/sessions/manager.py), [runtime.py](../sable/runtime.py) | [test_sessions.py](../tests/test_sessions.py), [test_runtime.py](../tests/test_runtime.py) | Bounded local summaries/traces and token/latency fields; no interrupted-task replay or price-based routing |
| CLI and one-shot automation | [cli_app.py](../sable/cli_app.py), [cli_args.py](../sable/cli_args.py), [automation.py](../sable/automation.py), [doctor.py](../sable/doctor.py) | [test_cli_app.py](../tests/test_cli_app.py), [test_automation.py](../tests/test_automation.py), [test_doctor.py](../tests/test_doctor.py) | Interactive CLI, final JSON/exit contract, offline doctor; no shipped IDE extension or local HTTP server |

## Evaluation and quality evidence

- [Scenario catalogs](../evals/scenarios) define the controlled inputs and expected
  outcomes; [fixtures](../evals/fixtures) contain synthetic repositories.
- [The baseline](../evals/baselines/m7-deterministic.json) contains 53 scenarios,
  minimum rate/context thresholds, and one permitted symlink-platform skip. These
  are acceptance requirements, not an observed run or live-model success rate.
- [The runner](../sable/evals/runner.py), [system executor](../sable/evals/system.py),
  [assertions](../sable/evals/assertions.py), and
  [reporting](../sable/evals/reporting.py) produce the evidence. See
  [evaluation methodology](../evals/README.md) and [reproducible demos](demo.md).
- [CI](../.github/workflows/ci.yml) configures Linux Python 3.10–3.13 tests,
  Python 3.13 coverage/package/security checks, deterministic evaluation, and
  Windows Python 3.13 installed-wheel smoke. These are distinct scopes.
- [pyproject.toml](../pyproject.toml) defines the 82% statement-coverage floor and
  tool settings. [CodeQL](../.github/workflows/codeql.yml) configures a separate
  scan. See [quality policy](quality.md) for limits and reproduction commands.
- [Release validation](../.github/workflows/release.yml) is manually dispatched;
  publication has separate guards and defaults off. Configuration does not prove
  a tag, GitHub Release, or PyPI publication exists. Follow [releasing](releasing.md).

## Rules for reporting results

1. Identify the source commit, date, platform, interpreter, command, and evidence
   location for a measured result. A result at an older SHA is historical evidence.
2. Distinguish scenario assertion passes from successful coding tasks. An expected
   denial or undo conflict can be a passing scenario.
3. Keep skips and missing evidence visible; include rate numerators and denominators.
4. Separate scripted deterministic runs from hosted/live-model runs. Never infer a
   live success rate from scripted responses.
5. Label the [roadmap](roadmap.md) as proposed work. Do not claim multi-provider,
   offline inference, durable task resume, plugins, a server, IDE integration, or
   autonomous multi-agent execution until implementation and validation exist.
6. Report the selected backend's actual guarantees. Neither native nor PRoot
   execution supplies kernel filesystem/network isolation.

From a checkout with developer dependencies installed, run:

```bash
python -m scripts.repo_check
python -m unittest discover -s tests
python -m sable.evals --baseline evals/baselines/m7-deterministic.json --output evals/reports/generated/local
```

The full local gate is `python -m scripts.quality`. Generated evaluation reports
stay untracked under `evals/reports/generated/`; cite a recorded run explicitly
rather than copying undated percentages into permanent product copy.
