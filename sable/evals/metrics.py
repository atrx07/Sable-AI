"""Deterministic aggregate metrics derived only from structured eval results."""

from __future__ import annotations

import statistics
from collections import Counter
from typing import Callable, Iterable

from .models import EvalDisposition, EvalResult, ExpectedOutcome, ScenarioCategory


def _rate(results: list[EvalResult], predicate: Callable[[EvalResult], bool]) -> dict[str, float | int]:
    denominator = len(results)
    numerator = sum(1 for item in results if predicate(item))
    return {
        "numerator": numerator,
        "denominator": denominator,
        "percent": round((100.0 * numerator / denominator), 2) if denominator else 0.0,
    }


def _distribution(values: Iterable[float | int]) -> dict[str, float | int]:
    data = [float(value) for value in values]
    if not data:
        return {"count": 0, "mean": 0.0, "median": 0.0}
    return {
        "count": len(data),
        "mean": round(statistics.fmean(data), 3),
        "median": round(statistics.median(data), 3),
    }


def aggregate_metrics(results: Iterable[EvalResult]) -> dict:
    items = list(results)
    completed = [item for item in items if item.disposition in {EvalDisposition.PASS, EvalDisposition.FAIL}]
    functional = [item for item in completed if item.expected_outcome == ExpectedOutcome.TASK_PASS]
    refusal = [item for item in completed if item.expected_outcome in {
        ExpectedOutcome.SAFE_REFUSAL, ExpectedOutcome.CAPABILITY_DENIED,
    }]
    security = [item for item in completed if item.category in {
        ScenarioCategory.SECURITY, ScenarioCategory.PROMPT_INJECTION, ScenarioCategory.CAPABILITY,
    }]
    rollback = [item for item in completed if item.expected_outcome in {
        ExpectedOutcome.ROLLBACK_SUCCESS, ExpectedOutcome.ROLLBACK_CONFLICT,
    }]
    repairs = [item for item in completed if item.category == ScenarioCategory.REPAIR]
    integrity = [item for item in completed if item.category == ScenarioCategory.INTEGRITY]
    verified = [item for item in functional if item.verification_status.upper() != "SKIPPED"]
    context = [item for item in completed if item.context_metrics.get("required_context_files", 0)]
    return {
        "scenario_pass_rate": _rate(completed, lambda item: item.passed),
        "task_success_rate": _rate(functional, lambda item: item.passed),
        "verified_success_rate": _rate(verified, lambda item: item.passed and item.verified),
        "safe_refusal_rate": _rate(refusal, lambda item: item.passed),
        "security_scenario_pass_rate": _rate(security, lambda item: item.passed),
        "rollback_correctness_rate": _rate(rollback, lambda item: item.passed),
        "repair_success_rate": _rate(repairs, lambda item: item.passed),
        "integrity_detection_rate": _rate(integrity, lambda item: item.passed),
        "model_turns": _distribution(item.model_turns for item in completed),
        "tool_calls": _distribution(item.tool_calls for item in completed),
        "task_duration_ms": _distribution(item.duration_ms for item in completed),
        "total_tokens": _distribution(item.token_usage.get("total_tokens", 0) for item in completed),
        "fixture_context_recall": _distribution(
            item.context_metrics.get("fixture_context_recall", 0) for item in context
        ),
        "fixture_context_precision": _distribution(
            item.context_metrics.get("fixture_context_precision", 0) for item in context
        ),
        "failure_reasons": dict(sorted(Counter(
            item.termination_reason or "ASSERTION_FAILURE"
            for item in completed if not item.passed
        ).items())),
        "dispositions": dict(sorted(Counter(item.disposition.value for item in items).items())),
        "skipped_count": sum(1 for item in items if item.disposition not in {EvalDisposition.PASS, EvalDisposition.FAIL}),
    }


__all__ = ["aggregate_metrics"]
