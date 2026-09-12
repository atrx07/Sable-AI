"""Machine-checkable evaluation assertions over temporary fixture workspaces."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .models import AssertionKind, AssertionResult, EvalAssertion, EvalScenario, ScenarioExecution


def _path(root: Path, target: str) -> Path:
    candidate = (root / target).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"assertion target escapes workspace: {target}") from exc
    return candidate


def _fingerprint(path: Path) -> str | None:
    if not path.is_file() or path.is_symlink():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _lookup(value: Any, dotted: str) -> Any:
    current = value
    for part in dotted.split(".") if dotted else []:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, (list, tuple)) and part.isdigit():
            index = int(part)
            current = current[index] if index < len(current) else None
        else:
            return None
    return current


def _event_names(events: list[dict[str, Any]]) -> list[str]:
    names = []
    for event in events:
        if not isinstance(event, dict):
            continue
        names.append(str(event.get("event_type", event.get("type", ""))).upper())
    return names


def evaluate_assertion(
    assertion: EvalAssertion,
    *,
    workspace: Path,
    baseline: dict[str, str],
    execution: ScenarioExecution,
) -> AssertionResult:
    kind = assertion.kind
    target = assertion.target.replace("\\", "/")
    passed = False
    detail = ""
    try:
        if kind == AssertionKind.FILE_EXISTS:
            passed = _path(workspace, target).is_file()
            detail = "file exists" if passed else "file is absent"
        elif kind == AssertionKind.FILE_ABSENT:
            passed = not _path(workspace, target).exists()
            detail = "path is absent" if passed else "path exists"
        elif kind == AssertionKind.FILE_CONTAINS:
            path = _path(workspace, target)
            content = path.read_text(encoding="utf-8") if path.is_file() else ""
            expected = str(assertion.expected or "")
            passed = bool(expected) and expected in content
            detail = "expected text found" if passed else "expected text not found"
        elif kind == AssertionKind.FILE_UNCHANGED:
            before = baseline.get(target)
            after = _fingerprint(_path(workspace, target))
            passed = before is not None and before == after
            detail = "fingerprint unchanged" if passed else "fingerprint changed or baseline missing"
        elif kind == AssertionKind.RESULT_EQUALS:
            actual = _lookup(execution.runtime_result, target)
            passed = actual == assertion.expected
            detail = f"actual={actual!r}; expected={assertion.expected!r}"
        elif kind in {AssertionKind.RESULT_CONTAINS, AssertionKind.RESULT_NOT_CONTAINS}:
            actual = _lookup(execution.runtime_result, target)
            contains = assertion.expected in actual if isinstance(actual, (str, list, tuple, set, dict)) else False
            passed = contains if kind == AssertionKind.RESULT_CONTAINS else not contains
            detail = f"actual={actual!r}; member={assertion.expected!r}"
        elif kind == AssertionKind.EVENT_OCCURRED:
            expected = str(assertion.expected or target).upper()
            passed = expected in _event_names(execution.events)
            detail = f"event {expected} {'observed' if passed else 'not observed'}"
        elif kind == AssertionKind.EXIT_CODE_EQUALS:
            expected = int(assertion.expected)
            passed = execution.exit_code == expected
            detail = f"actual={execution.exit_code!r}; expected={expected!r}"
        else:
            detail = f"unsupported assertion kind: {kind.value}"
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        detail = f"assertion error: {exc}"
    return AssertionResult(kind.value, target, passed, detail)


def evaluate_scenario(
    scenario: EvalScenario,
    *,
    workspace: Path,
    baseline: dict[str, str],
    execution: ScenarioExecution,
    duration_ms: int,
) -> tuple[list[AssertionResult], list[str]]:
    results = [AssertionResult(
        "OUTCOME_EQUALS",
        "outcome",
        execution.outcome == scenario.expected_outcome,
        f"actual={execution.outcome.value}; expected={scenario.expected_outcome.value}",
    )]
    changed = {path.replace("\\", "/") for path in execution.changed_files}
    for target in scenario.expected_changed_files:
        normalized = target.replace("\\", "/")
        results.append(AssertionResult(
            "EXPECTED_CHANGED_FILE", normalized, normalized in changed,
            "reported changed" if normalized in changed else "not reported changed",
        ))
    forbidden_changes: list[str] = []
    for target in scenario.forbidden_changed_files:
        normalized = target.replace("\\", "/")
        current = _fingerprint(_path(workspace, normalized))
        forbidden = normalized in changed or baseline.get(normalized) != current
        if forbidden:
            forbidden_changes.append(normalized)
        results.append(AssertionResult(
            "FORBIDDEN_FILE_UNCHANGED", normalized, not forbidden,
            "unchanged" if not forbidden else "forbidden file changed",
        ))
    if scenario.expected_verification:
        expected = scenario.expected_verification.upper()
        actual = execution.verification_status.upper()
        results.append(AssertionResult(
            "VERIFICATION_EQUALS", "verification_status", actual == expected,
            f"actual={actual}; expected={expected}",
        ))
    observed_capabilities = {item.upper() for item in execution.capability_events}
    for capability in scenario.expected_capabilities:
        expected = capability.upper()
        results.append(AssertionResult(
            "CAPABILITY_OCCURRED", expected, expected in observed_capabilities,
            "observed" if expected in observed_capabilities else "not observed",
        ))
    runtime = execution.runtime_result.get("runtime_task", {})
    termination_reason = str(runtime.get("termination_reason", "")) if isinstance(runtime, dict) else ""
    # MainAgent permits one final, tools-disabled summary call after a hard
    # decision-turn ceiling. That call cannot execute another action.
    model_turn_ceiling = scenario.max_model_turns + (
        1 if termination_reason == "MODEL_TURN_LIMIT" else 0
    )
    results.extend([
        AssertionResult(
            "MODEL_TURN_BUDGET", "model_turns", execution.model_turns <= model_turn_ceiling,
            f"actual={execution.model_turns}; maximum={model_turn_ceiling}",
        ),
        AssertionResult(
            "TOOL_CALL_BUDGET", "tool_calls", execution.tool_calls <= scenario.max_tool_calls,
            f"actual={execution.tool_calls}; maximum={scenario.max_tool_calls}",
        ),
        AssertionResult(
            "DURATION_BUDGET", "duration_ms", duration_ms <= scenario.max_duration_ms,
            f"actual={duration_ms}; maximum={scenario.max_duration_ms}",
        ),
    ])
    results.extend(
        evaluate_assertion(item, workspace=workspace, baseline=baseline, execution=execution)
        for item in scenario.assertions
    )
    return results, forbidden_changes


__all__ = ["evaluate_assertion", "evaluate_scenario"]
