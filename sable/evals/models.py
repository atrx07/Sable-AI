"""Typed contracts for deterministic and explicitly invoked live evaluations."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any

from ..config import redact_secrets


SCHEMA_VERSION = 1
SCENARIO_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")


class EvalMode(str, Enum):
    DETERMINISTIC = "DETERMINISTIC"
    LIVE = "LIVE"


class ScenarioCategory(str, Enum):
    CODING = "CODING"
    MULTI_FILE = "MULTI_FILE"
    REPAIR = "REPAIR"
    CONTEXT = "CONTEXT"
    VERIFICATION = "VERIFICATION"
    TRANSACTION = "TRANSACTION"
    SECURITY = "SECURITY"
    PROMPT_INJECTION = "PROMPT_INJECTION"
    CAPABILITY = "CAPABILITY"
    INTEGRITY = "INTEGRITY"
    CANCELLATION = "CANCELLATION"
    BUDGET = "BUDGET"
    AUTOMATION = "AUTOMATION"
    FAILURE_HANDLING = "FAILURE_HANDLING"


class ExpectedOutcome(str, Enum):
    TASK_PASS = "TASK_PASS"
    TASK_FAIL = "TASK_FAIL"
    SAFE_REFUSAL = "SAFE_REFUSAL"
    CAPABILITY_DENIED = "CAPABILITY_DENIED"
    VERIFICATION_FAIL = "VERIFICATION_FAIL"
    VERIFICATION_INCOMPLETE = "VERIFICATION_INCOMPLETE"
    ROLLBACK_SUCCESS = "ROLLBACK_SUCCESS"
    ROLLBACK_CONFLICT = "ROLLBACK_CONFLICT"
    CANCELLED = "CANCELLED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"


class EvalDisposition(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIPPED_PLATFORM = "SKIPPED_PLATFORM"
    SKIPPED_TOOL_UNAVAILABLE = "SKIPPED_TOOL_UNAVAILABLE"
    SKIPPED_NOT_REQUESTED = "SKIPPED_NOT_REQUESTED"
    SKIPPED_LIVE_DISABLED = "SKIPPED_LIVE_DISABLED"


class VerificationFixtureState(str, Enum):
    PASS_WITH_OPTIONAL_SKIPS = "PASS_WITH_OPTIONAL_SKIPS"
    INCOMPLETE = "INCOMPLETE"
    BLOCKED = "BLOCKED"
    TYPE_ERROR = "TYPE_ERROR"


class AssertionKind(str, Enum):
    FILE_EXISTS = "FILE_EXISTS"
    FILE_ABSENT = "FILE_ABSENT"
    FILE_CONTAINS = "FILE_CONTAINS"
    FILE_UNCHANGED = "FILE_UNCHANGED"
    RESULT_EQUALS = "RESULT_EQUALS"
    RESULT_CONTAINS = "RESULT_CONTAINS"
    RESULT_NOT_CONTAINS = "RESULT_NOT_CONTAINS"
    EVENT_OCCURRED = "EVENT_OCCURRED"
    EXIT_CODE_EQUALS = "EXIT_CODE_EQUALS"


def _enum(enum_type, value: Any, field_name: str):
    try:
        return enum_type(str(value).upper())
    except ValueError as exc:
        choices = ", ".join(item.value for item in enum_type)
        raise ValueError(f"Invalid {field_name} {value!r}; expected one of: {choices}") from exc


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be a list of strings")
    return tuple(value)


def _safe(value: Any, limit: int = 1000) -> str:
    return redact_secrets(str(value or ""))[:limit]


def _confined_path(value: Any, field_name: str) -> str:
    path = str(value or "").replace("\\", "/")
    candidate = PurePosixPath(path)
    if not path or candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"{field_name} must be a confined relative path")
    return path


@dataclass(frozen=True)
class EvalAssertion:
    kind: AssertionKind
    target: str = ""
    expected: Any = None

    @classmethod
    def from_dict(cls, value: Any) -> "EvalAssertion":
        if not isinstance(value, dict):
            raise ValueError("assertion must be an object")
        return cls(
            kind=_enum(AssertionKind, value.get("kind", ""), "assertion kind"),
            target=str(value.get("target", "")),
            expected=value.get("expected"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "target": self.target, "expected": self.expected}


@dataclass(frozen=True)
class EvalFileWrite:
    """A synthetic user-side write performed outside Sable's file tools."""

    path: str
    content: str

    def __post_init__(self) -> None:
        _confined_path(self.path, "file write path")

    @classmethod
    def from_dict(cls, value: Any) -> "EvalFileWrite":
        if not isinstance(value, dict):
            raise ValueError("file write must be an object")
        return cls(
            path=_confined_path(value.get("path"), "file write path"),
            content=str(value.get("content", "")),
        )

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "content": self.content}


@dataclass(frozen=True)
class EvalScenario:
    scenario_id: str
    title: str
    category: ScenarioCategory
    description: str
    fixture: str
    task_prompt: str
    expected_outcome: ExpectedOutcome
    mode: EvalMode = EvalMode.DETERMINISTIC
    assertions: tuple[EvalAssertion, ...] = ()
    provider_script: tuple[dict[str, Any], ...] = ()
    expected_verification: str | None = None
    expected_changed_files: tuple[str, ...] = ()
    forbidden_changed_files: tuple[str, ...] = ()
    expected_capabilities: tuple[str, ...] = ()
    approved_capabilities: tuple[str, ...] = ()
    required_context_files: tuple[str, ...] = ()
    verification_command: str | None = None
    verification_scope: str = "AFFECTED"
    runtime_mode: str = "build"
    verification_fixture_state: VerificationFixtureState | None = None
    max_repair_loops: int = 2
    initialize_git: bool = False
    pre_run_writes: tuple[EvalFileWrite, ...] = ()
    post_run_writes: tuple[EvalFileWrite, ...] = ()
    undo_after_run: bool = False
    max_model_turns: int = 12
    max_tool_calls: int = 24
    max_duration_ms: int = 30000
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not SCENARIO_ID.fullmatch(self.scenario_id):
            raise ValueError("scenario_id must be 3-80 lowercase identifier characters")
        if not self.title.strip() or not self.description.strip() or not self.task_prompt.strip():
            raise ValueError("title, description, and task_prompt are required")
        fixture = PurePosixPath(self.fixture.replace("\\", "/"))
        if fixture.is_absolute() or not self.fixture or ".." in fixture.parts:
            raise ValueError("fixture must be a confined relative path")
        for name, number in (
            ("max_repair_loops", self.max_repair_loops),
            ("max_model_turns", self.max_model_turns),
            ("max_tool_calls", self.max_tool_calls),
            ("max_duration_ms", self.max_duration_ms),
        ):
            if not isinstance(number, int) or number < (0 if name == "max_repair_loops" else 1):
                qualifier = "non-negative" if name == "max_repair_loops" else "positive"
                raise ValueError(f"{name} must be a {qualifier} integer")
        if self.verification_scope.upper() not in {"QUICK", "AFFECTED", "FULL"}:
            raise ValueError("verification_scope must be QUICK, AFFECTED, or FULL")
        if self.runtime_mode.lower() not in {"build", "yolo"}:
            raise ValueError("runtime_mode must be build or yolo")

    @classmethod
    def from_dict(cls, value: Any) -> "EvalScenario":
        if not isinstance(value, dict):
            raise ValueError("scenario must be an object")
        required = ("scenario_id", "title", "category", "description", "fixture", "task_prompt", "expected_outcome")
        missing = [name for name in required if name not in value]
        if missing:
            raise ValueError(f"scenario is missing required fields: {', '.join(missing)}")
        assertions = value.get("assertions", [])
        script = value.get("provider_script", [])
        pre_writes = value.get("pre_run_writes", [])
        post_writes = value.get("post_run_writes", [])
        if (
            not isinstance(assertions, list)
            or not isinstance(script, list)
            or not all(isinstance(item, dict) for item in script)
            or not isinstance(pre_writes, list)
            or not isinstance(post_writes, list)
        ):
            raise ValueError("assertions and provider_script must be lists of objects")
        return cls(
            scenario_id=str(value["scenario_id"]),
            title=str(value["title"]),
            category=_enum(ScenarioCategory, value["category"], "category"),
            description=str(value["description"]),
            fixture=str(value["fixture"]),
            task_prompt=str(value["task_prompt"]),
            expected_outcome=_enum(ExpectedOutcome, value["expected_outcome"], "expected outcome"),
            mode=_enum(EvalMode, value.get("mode", "DETERMINISTIC"), "mode"),
            assertions=tuple(EvalAssertion.from_dict(item) for item in assertions),
            provider_script=tuple(dict(item) for item in script),
            expected_verification=(str(value["expected_verification"]) if value.get("expected_verification") else None),
            expected_changed_files=_string_tuple(value.get("expected_changed_files"), "expected_changed_files"),
            forbidden_changed_files=_string_tuple(value.get("forbidden_changed_files"), "forbidden_changed_files"),
            expected_capabilities=_string_tuple(value.get("expected_capabilities"), "expected_capabilities"),
            approved_capabilities=_string_tuple(value.get("approved_capabilities"), "approved_capabilities"),
            required_context_files=_string_tuple(value.get("required_context_files"), "required_context_files"),
            verification_command=(str(value["verification_command"]) if value.get("verification_command") else None),
            verification_scope=str(value.get("verification_scope", "AFFECTED")).upper(),
            runtime_mode=str(value.get("runtime_mode", "build")).lower(),
            verification_fixture_state=(
                _enum(VerificationFixtureState, value["verification_fixture_state"], "verification fixture state")
                if value.get("verification_fixture_state") else None
            ),
            max_repair_loops=int(value.get("max_repair_loops", 2)),
            initialize_git=bool(value.get("initialize_git", False)),
            pre_run_writes=tuple(EvalFileWrite.from_dict(item) for item in pre_writes),
            post_run_writes=tuple(EvalFileWrite.from_dict(item) for item in post_writes),
            undo_after_run=bool(value.get("undo_after_run", False)),
            max_model_turns=int(value.get("max_model_turns", 12)),
            max_tool_calls=int(value.get("max_tool_calls", 24)),
            max_duration_ms=int(value.get("max_duration_ms", 30000)),
            tags=_string_tuple(value.get("tags"), "tags"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "title": self.title,
            "category": self.category.value,
            "description": self.description,
            "fixture": self.fixture,
            "task_prompt": self.task_prompt,
            "expected_outcome": self.expected_outcome.value,
            "mode": self.mode.value,
            "assertions": [item.to_dict() for item in self.assertions],
            "provider_script": [dict(item) for item in self.provider_script],
            "expected_verification": self.expected_verification,
            "expected_changed_files": list(self.expected_changed_files),
            "forbidden_changed_files": list(self.forbidden_changed_files),
            "expected_capabilities": list(self.expected_capabilities),
            "approved_capabilities": list(self.approved_capabilities),
            "required_context_files": list(self.required_context_files),
            "verification_command": self.verification_command,
            "verification_scope": self.verification_scope,
            "runtime_mode": self.runtime_mode,
            "verification_fixture_state": (
                self.verification_fixture_state.value if self.verification_fixture_state else None
            ),
            "max_repair_loops": self.max_repair_loops,
            "initialize_git": self.initialize_git,
            "pre_run_writes": [item.to_dict() for item in self.pre_run_writes],
            "post_run_writes": [item.to_dict() for item in self.post_run_writes],
            "undo_after_run": self.undo_after_run,
            "max_model_turns": self.max_model_turns,
            "max_tool_calls": self.max_tool_calls,
            "max_duration_ms": self.max_duration_ms,
            "tags": list(self.tags),
        }


@dataclass
class ScenarioExecution:
    outcome: ExpectedOutcome
    runtime_result: dict[str, Any] = field(default_factory=dict)
    exit_code: int | None = None
    changed_files: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    verified: bool = False
    verification_status: str = "SKIPPED"
    transaction_status: str | None = None
    rollback_status: str | None = None
    capability_events: list[str] = field(default_factory=list)
    token_usage: dict[str, int] = field(default_factory=dict)
    context_metrics: dict[str, float | int] = field(default_factory=dict)
    model_turns: int = 0
    tool_calls: int = 0
    repair_loops: int = 0
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AssertionResult:
    kind: str
    target: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "target": self.target,
            "passed": self.passed,
            "detail": _safe(self.detail),
        }


@dataclass
class EvalResult:
    scenario_id: str
    category: ScenarioCategory
    mode: EvalMode
    expected_outcome: ExpectedOutcome
    actual_outcome: ExpectedOutcome | None
    disposition: EvalDisposition
    started_at: str
    duration_ms: int
    assertions: list[AssertionResult] = field(default_factory=list)
    runtime_status: str | None = None
    termination_reason: str | None = None
    verified: bool = False
    verification_status: str = "SKIPPED"
    changed_files: list[str] = field(default_factory=list)
    forbidden_changes: list[str] = field(default_factory=list)
    model_turns: int = 0
    tool_calls: int = 0
    repair_loops: int = 0
    transaction_status: str | None = None
    rollback_status: str | None = None
    capability_events: list[str] = field(default_factory=list)
    token_usage: dict[str, int] = field(default_factory=dict)
    context_metrics: dict[str, float | int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.disposition == EvalDisposition.PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "scenario_id": self.scenario_id,
            "category": self.category.value,
            "mode": self.mode.value,
            "expected_outcome": self.expected_outcome.value,
            "actual_outcome": self.actual_outcome.value if self.actual_outcome else None,
            "disposition": self.disposition.value,
            "passed": self.passed,
            "started_at": self.started_at,
            "duration_ms": max(0, int(self.duration_ms)),
            "assertions": [item.to_dict() for item in self.assertions],
            "runtime_status": _safe(self.runtime_status) or None,
            "termination_reason": _safe(self.termination_reason) or None,
            "verified": bool(self.verified),
            "verification_status": _safe(self.verification_status, 80),
            "changed_files": [_safe(item, 500) for item in self.changed_files[:500]],
            "forbidden_changes": [_safe(item, 500) for item in self.forbidden_changes[:500]],
            "model_turns": max(0, int(self.model_turns)),
            "tool_calls": max(0, int(self.tool_calls)),
            "repair_loops": max(0, int(self.repair_loops)),
            "transaction_status": _safe(self.transaction_status) or None,
            "rollback_status": _safe(self.rollback_status) or None,
            "capability_events": [_safe(item, 200) for item in self.capability_events[:100]],
            "token_usage": {str(key): max(0, int(value)) for key, value in self.token_usage.items()},
            "context_metrics": dict(self.context_metrics),
            "errors": [_safe(item) for item in self.errors[:50]],
            "notes": [_safe(item) for item in self.notes[:50]],
        }


def load_scenario_file(path: str | Path) -> EvalScenario:
    import json

    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to load scenario {source.name}: {exc}") from exc
    return EvalScenario.from_dict(value)


def load_scenario_suite(path: str | Path) -> list[EvalScenario]:
    """Load an ordered JSON array of typed scenarios and reject duplicate IDs."""
    import json

    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to load scenario suite {source.name}: {exc}") from exc
    if not isinstance(value, list):
        raise ValueError("scenario suite must be a JSON array")
    scenarios = [EvalScenario.from_dict(item) for item in value]
    identifiers = [item.scenario_id for item in scenarios]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("scenario suite contains duplicate scenario IDs")
    return scenarios


__all__ = [
    "AssertionKind", "AssertionResult", "EvalAssertion", "EvalDisposition", "EvalFileWrite", "EvalMode",
    "EvalResult", "EvalScenario", "ExpectedOutcome", "SCHEMA_VERSION", "ScenarioCategory",
    "ScenarioExecution", "VerificationFixtureState", "load_scenario_file", "load_scenario_suite",
]
