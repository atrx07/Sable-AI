"""Bounded scenario runner for temporary fixtures and scripted providers."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from ..config import redact_secrets
from .assertions import evaluate_scenario
from .fixtures import FixtureManager
from .models import (
    EvalDisposition,
    EvalMode,
    EvalResult,
    EvalScenario,
    ScenarioExecution,
)
from .provider import ScriptedProvider


ScenarioExecutor = Callable[[EvalScenario, Path, ScriptedProvider], ScenarioExecution]


class EvaluationSkip(RuntimeError):
    def __init__(self, disposition: EvalDisposition, reason: str):
        if disposition in {EvalDisposition.PASS, EvalDisposition.FAIL}:
            raise ValueError("EvaluationSkip requires an explicit skipped disposition")
        super().__init__(reason)
        self.disposition = disposition
        self.reason = reason


class EvaluationRunner:
    def __init__(
        self,
        fixtures_root: str | Path,
        *,
        allow_live: bool = False,
        provider_factory=ScriptedProvider,
    ):
        self.fixtures = FixtureManager(fixtures_root)
        self.allow_live = bool(allow_live)
        self.provider_factory = provider_factory

    def _skipped(self, scenario: EvalScenario, disposition: EvalDisposition, reason: str) -> EvalResult:
        return EvalResult(
            scenario_id=scenario.scenario_id,
            category=scenario.category,
            mode=scenario.mode,
            expected_outcome=scenario.expected_outcome,
            actual_outcome=None,
            disposition=disposition,
            started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            duration_ms=0,
            notes=[redact_secrets(reason)[:1000]],
        )

    def run(self, scenario: EvalScenario, executor: ScenarioExecutor) -> EvalResult:
        if scenario.mode == EvalMode.LIVE and not self.allow_live:
            return self._skipped(
                scenario,
                EvalDisposition.SKIPPED_LIVE_DISABLED,
                "Live evaluation was not explicitly enabled.",
            )
        started_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        started = time.monotonic()
        try:
            with self.fixtures.materialize(scenario.fixture) as materialized:
                provider = self.provider_factory(scenario.provider_script)
                execution = executor(scenario, materialized.path, provider)
                if not isinstance(execution, ScenarioExecution):
                    raise TypeError("scenario executor must return ScenarioExecution")
                duration_ms = max(0, int((time.monotonic() - started) * 1000))
                assertions, forbidden = evaluate_scenario(
                    scenario,
                    workspace=materialized.path,
                    baseline=materialized.baseline,
                    execution=execution,
                    duration_ms=duration_ms,
                )
        except EvaluationSkip as exc:
            return self._skipped(scenario, exc.disposition, exc.reason)
        except Exception as exc:
            duration_ms = max(0, int((time.monotonic() - started) * 1000))
            return EvalResult(
                scenario_id=scenario.scenario_id,
                category=scenario.category,
                mode=scenario.mode,
                expected_outcome=scenario.expected_outcome,
                actual_outcome=None,
                disposition=EvalDisposition.FAIL,
                started_at=started_at,
                duration_ms=duration_ms,
                errors=[redact_secrets(str(exc))[:1000]],
            )

        runtime = execution.runtime_result.get("runtime_task", {})
        if not isinstance(runtime, dict):
            runtime = {}
        failures = [item for item in assertions if not item.passed]
        errors = [redact_secrets(str(item))[:1000] for item in execution.errors[:50]]
        return EvalResult(
            scenario_id=scenario.scenario_id,
            category=scenario.category,
            mode=scenario.mode,
            expected_outcome=scenario.expected_outcome,
            actual_outcome=execution.outcome,
            disposition=EvalDisposition.PASS if not failures and not errors else EvalDisposition.FAIL,
            started_at=started_at,
            duration_ms=duration_ms,
            assertions=assertions,
            runtime_status=str(execution.runtime_result.get("final_status", "")) or None,
            termination_reason=str(runtime.get("termination_reason", "")) or None,
            verified=execution.verified,
            verification_status=execution.verification_status,
            changed_files=list(execution.changed_files),
            forbidden_changes=forbidden,
            model_turns=execution.model_turns,
            tool_calls=execution.tool_calls,
            repair_loops=execution.repair_loops,
            transaction_status=execution.transaction_status,
            rollback_status=execution.rollback_status,
            capability_events=list(execution.capability_events),
            token_usage=dict(execution.token_usage),
            context_metrics=dict(execution.context_metrics),
            errors=errors,
            notes=list(execution.notes),
        )

    def run_many(self, scenarios: Iterable[EvalScenario], executor: ScenarioExecutor) -> list[EvalResult]:
        seen: set[str] = set()
        results: list[EvalResult] = []
        for scenario in scenarios:
            if scenario.scenario_id in seen:
                raise ValueError(f"duplicate scenario id: {scenario.scenario_id}")
            seen.add(scenario.scenario_id)
            results.append(self.run(scenario, executor))
        return results


__all__ = ["EvaluationRunner", "EvaluationSkip", "ScenarioExecutor"]
