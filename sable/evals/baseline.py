"""Stable deterministic baseline loading and comparison."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import EvalDisposition, EvalMode, SCHEMA_VERSION


BASELINE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class BaselineComparison:
    baseline_id: str
    passed: bool
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": BASELINE_SCHEMA_VERSION,
            "baseline_id": self.baseline_id,
            "passed": self.passed,
            "errors": list(self.errors),
        }


def load_baseline(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to load baseline {source.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("baseline must be a JSON object")
    if value.get("schema_version") != BASELINE_SCHEMA_VERSION:
        raise ValueError(f"baseline schema_version must be {BASELINE_SCHEMA_VERSION}")
    if value.get("eval_schema_version") != SCHEMA_VERSION:
        raise ValueError(f"baseline eval_schema_version must be {SCHEMA_VERSION}")
    if not isinstance(value.get("baseline_id"), str) or not value["baseline_id"].strip():
        raise ValueError("baseline_id must be a non-empty string")
    scenario_ids = value.get("scenario_ids")
    if (
        not isinstance(scenario_ids, list)
        or not scenario_ids
        or not all(isinstance(item, str) and item for item in scenario_ids)
        or len(scenario_ids) != len(set(scenario_ids))
    ):
        raise ValueError("scenario_ids must be a non-empty unique string list")
    allowed = value.get("allowed_platform_skips", [])
    if not isinstance(allowed, list) or not all(isinstance(item, str) for item in allowed):
        raise ValueError("allowed_platform_skips must be a string list")
    if not set(allowed) <= set(scenario_ids):
        raise ValueError("allowed_platform_skips must be present in scenario_ids")
    maximum = value.get("maximum_platform_skips", 0)
    if not isinstance(maximum, int) or maximum < 0:
        raise ValueError("maximum_platform_skips must be a non-negative integer")
    if maximum > len(allowed):
        raise ValueError("maximum_platform_skips cannot exceed the allowed skip set")
    for field in ("minimum_rates", "minimum_distribution_means"):
        thresholds = value.get(field, {})
        if not isinstance(thresholds, dict) or not all(
            isinstance(name, str) and isinstance(limit, (int, float))
            for name, limit in thresholds.items()
        ):
            raise ValueError(f"{field} must map metric names to numeric thresholds")
        if field == "minimum_rates" and any(not 0 <= float(limit) <= 100 for limit in thresholds.values()):
            raise ValueError("minimum_rates thresholds must be between 0 and 100")
    return value


def compare_baseline(report: dict[str, Any], baseline: dict[str, Any]) -> BaselineComparison:
    errors: list[str] = []
    baseline_id = str(baseline.get("baseline_id", "unknown"))
    if report.get("schema_version") != baseline.get("eval_schema_version"):
        errors.append("evaluation report schema does not match the baseline")
    if str(report.get("mode", "")).upper() != EvalMode.DETERMINISTIC.value:
        errors.append("canonical baseline comparison requires DETERMINISTIC mode")

    results = report.get("results", [])
    if not isinstance(results, list):
        results = []
        errors.append("evaluation report results must be a list")
    actual_ids = [str(item.get("scenario_id", "")) for item in results if isinstance(item, dict)]
    expected_ids = list(baseline.get("scenario_ids", []))
    if len(actual_ids) != len(set(actual_ids)):
        errors.append("evaluation report contains duplicate scenario IDs")
    if report.get("scenario_count") != len(actual_ids):
        errors.append("evaluation report scenario_count does not match its results")
    missing = sorted(set(expected_ids) - set(actual_ids))
    extra = sorted(set(actual_ids) - set(expected_ids))
    if missing:
        errors.append("missing scenarios: " + ", ".join(missing))
    if extra:
        errors.append("unexpected scenarios: " + ", ".join(extra))

    failed = [
        item.get("scenario_id") for item in results
        if isinstance(item, dict) and item.get("disposition") == EvalDisposition.FAIL.value
    ]
    if failed:
        errors.append("failed scenarios: " + ", ".join(str(item) for item in failed))
    skips = [
        item for item in results
        if isinstance(item, dict) and str(item.get("disposition", "")).startswith("SKIPPED_")
    ]
    unsupported_skips = [
        str(item.get("scenario_id")) for item in skips
        if item.get("disposition") != EvalDisposition.SKIPPED_PLATFORM.value
    ]
    if unsupported_skips:
        errors.append("non-platform skips are not baseline-eligible: " + ", ".join(unsupported_skips))
    allowed_platform = set(baseline.get("allowed_platform_skips", []))
    unexpected_platform = [
        str(item.get("scenario_id")) for item in skips
        if item.get("disposition") == EvalDisposition.SKIPPED_PLATFORM.value
        and item.get("scenario_id") not in allowed_platform
    ]
    if unexpected_platform:
        errors.append("unexpected platform skips: " + ", ".join(unexpected_platform))
    if len(skips) > int(baseline.get("maximum_platform_skips", 0)):
        errors.append(
            f"platform skips {len(skips)} exceed maximum {baseline.get('maximum_platform_skips', 0)}"
        )

    metrics = report.get("metrics", {})
    if not isinstance(metrics, dict):
        metrics = {}
    for name, minimum in baseline.get("minimum_rates", {}).items():
        metric = metrics.get(name, {})
        actual = metric.get("percent") if isinstance(metric, dict) else None
        if not isinstance(actual, (int, float)) or float(actual) < float(minimum):
            errors.append(f"metric {name}={actual!r} is below minimum {minimum}")
    for name, minimum in baseline.get("minimum_distribution_means", {}).items():
        metric = metrics.get(name, {})
        actual = metric.get("mean") if isinstance(metric, dict) else None
        if not isinstance(actual, (int, float)) or float(actual) < float(minimum):
            errors.append(f"metric {name}.mean={actual!r} is below minimum {minimum}")
    return BaselineComparison(baseline_id, not errors, tuple(errors))


__all__ = [
    "BASELINE_SCHEMA_VERSION", "BaselineComparison", "compare_baseline", "load_baseline",
]
