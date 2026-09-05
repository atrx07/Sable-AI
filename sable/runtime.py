"""Explicit, validated runtime state for one Sable task execution."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .config import redact_secrets


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class RuntimeStateError(RuntimeError):
    """Raised when a task attempts an invalid runtime-state transition."""


class RuntimePhase(str, Enum):
    CREATED = "CREATED"
    DISCOVER = "DISCOVER"
    CONTEXT = "CONTEXT"
    PLAN = "PLAN"
    EXECUTE = "EXECUTE"
    VERIFY = "VERIFY"
    REPAIR = "REPAIR"
    REPORT = "REPORT"


class TerminalStatus(str, Enum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    ABORTED = "ABORTED"
    ROLLED_BACK = "ROLLED_BACK"


class TerminationReason(str, Enum):
    SUCCESS = "SUCCESS"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    TOOL_BUDGET_EXHAUSTED = "TOOL_BUDGET_EXHAUSTED"
    MODEL_TURN_LIMIT = "MODEL_TURN_LIMIT"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    USER_ABORT = "USER_ABORT"
    UNEXPECTED_ERROR = "UNEXPECTED_ERROR"
    ROLLBACK_COMPLETED = "ROLLBACK_COMPLETED"
    ROLLBACK_CONFLICT = "ROLLBACK_CONFLICT"


class RuntimeEventType(str, Enum):
    TASK_STARTED = "TASK_STARTED"
    PHASE_CHANGED = "PHASE_CHANGED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_FAILED = "TASK_FAILED"


ALLOWED_TRANSITIONS: dict[RuntimePhase, frozenset[RuntimePhase]] = {
    RuntimePhase.CREATED: frozenset({RuntimePhase.DISCOVER}),
    RuntimePhase.DISCOVER: frozenset({RuntimePhase.CONTEXT}),
    RuntimePhase.CONTEXT: frozenset({RuntimePhase.PLAN, RuntimePhase.EXECUTE, RuntimePhase.REPORT}),
    RuntimePhase.PLAN: frozenset({RuntimePhase.EXECUTE, RuntimePhase.REPORT}),
    RuntimePhase.EXECUTE: frozenset({RuntimePhase.VERIFY, RuntimePhase.REPORT}),
    RuntimePhase.VERIFY: frozenset({RuntimePhase.REPAIR, RuntimePhase.REPORT}),
    RuntimePhase.REPAIR: frozenset({RuntimePhase.VERIFY, RuntimePhase.REPORT}),
    RuntimePhase.REPORT: frozenset(),
}


@dataclass(frozen=True)
class RuntimeEvent:
    event_id: str
    timestamp: str
    event_type: RuntimeEventType
    phase: RuntimePhase
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "event_type": self.event_type.value,
            "phase": self.phase.value,
            "metadata": dict(self.metadata),
        }


@dataclass
class RuntimeTask:
    task_id: str
    user_request: str
    workspace_root: str
    created_at: str
    current_phase: RuntimePhase = RuntimePhase.CREATED
    terminal_status: TerminalStatus | None = None
    termination_reason: TerminationReason | None = None
    started_at: str | None = None
    completed_at: str | None = None
    transaction_id: str | None = None
    session_id: str | None = None
    repository: dict[str, Any] = field(default_factory=dict)
    selected_provider: str | None = None
    selected_main_model: str | None = None
    selected_fast_model: str | None = None
    model_turn_count: int = 0
    tool_call_count: int = 0
    repair_loop_count: int = 0
    verification: dict[str, Any] = field(default_factory=dict)
    changed_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_ms: int = 0
    events: list[RuntimeEvent] = field(default_factory=list)
    _started_monotonic: float | None = field(default=None, repr=False, compare=False)

    @classmethod
    def create(cls, user_request: str, workspace_root: str, *, session_id: str | None = None) -> "RuntimeTask":
        now = datetime.now(timezone.utc)
        return cls(
            task_id=f"task-{now:%Y%m%d%H%M%S}-{uuid.uuid4().hex[:8]}",
            user_request=redact_secrets(str(user_request))[:2000],
            workspace_root=str(workspace_root),
            created_at=now.isoformat(timespec="microseconds"),
            session_id=session_id,
        )

    @property
    def is_terminal(self) -> bool:
        return self.terminal_status is not None

    def _emit(self, event_type: RuntimeEventType, **metadata: Any) -> None:
        safe = {
            str(key)[:80]: redact_secrets(str(value))[:500]
            for key, value in metadata.items()
            if value is not None
        }
        self.events.append(RuntimeEvent(uuid.uuid4().hex, utc_now(), event_type, self.current_phase, safe))

    def start(self, reason: str = "task_accepted") -> None:
        if self.started_at is not None:
            raise RuntimeStateError("Runtime task has already started.")
        self.started_at = utc_now()
        self._started_monotonic = time.monotonic()
        self._emit(RuntimeEventType.TASK_STARTED, reason=reason)
        self.transition(RuntimePhase.DISCOVER, reason="runtime_started")

    def transition(self, phase: RuntimePhase, *, reason: str) -> None:
        if self.is_terminal:
            raise RuntimeStateError(f"Terminal task {self.task_id} cannot transition.")
        if not isinstance(phase, RuntimePhase):
            raise RuntimeStateError(f"Invalid runtime phase: {phase!r}")
        allowed = ALLOWED_TRANSITIONS[self.current_phase]
        if phase not in allowed:
            raise RuntimeStateError(f"Invalid runtime transition: {self.current_phase.value} -> {phase.value}")
        previous = self.current_phase
        self.current_phase = phase
        self._emit(RuntimeEventType.PHASE_CHANGED, **{"from": previous.value, "to": phase.value, "reason": reason})

    def terminate(
        self,
        status: TerminalStatus,
        reason: TerminationReason,
        *,
        error: str | None = None,
        allow_from_active_phase: bool = False,
    ) -> None:
        if self.is_terminal:
            raise RuntimeStateError(f"Terminal task {self.task_id} is immutable.")
        if self.current_phase != RuntimePhase.REPORT and not allow_from_active_phase:
            raise RuntimeStateError("Normal task termination is only valid from REPORT.")
        self.terminal_status = status
        self.termination_reason = reason
        self.completed_at = utc_now()
        if self._started_monotonic is not None:
            self.duration_ms = max(0, int((time.monotonic() - self._started_monotonic) * 1000))
        if error:
            self.errors.append(redact_secrets(str(error))[:1000])
        event_type = RuntimeEventType.TASK_COMPLETED if status == TerminalStatus.COMPLETED else RuntimeEventType.TASK_FAILED
        self._emit(event_type, status=status.value, reason=reason.value, error=error)

    def record_agent_result(self, result: dict[str, Any]) -> None:
        self.model_turn_count += int(result.get("model_calls", result.get("steps", 0)) or 0)
        self.tool_call_count += int(result.get("tool_calls", 0) or 0)
        self.changed_files = list(dict.fromkeys(self.changed_files + list(result.get("changed_files", []))))

    def to_dict(self, *, include_events: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "task_id": self.task_id,
            "user_request": self.user_request,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "current_phase": self.current_phase.value,
            "terminal_status": self.terminal_status.value if self.terminal_status else None,
            "termination_reason": self.termination_reason.value if self.termination_reason else None,
            "transaction_id": self.transaction_id,
            "session_id": self.session_id,
            "workspace_root": self.workspace_root,
            "repository": dict(self.repository),
            "selected_provider": self.selected_provider,
            "selected_main_model": self.selected_main_model,
            "selected_fast_model": self.selected_fast_model,
            "model_turn_count": self.model_turn_count,
            "tool_call_count": self.tool_call_count,
            "repair_loop_count": self.repair_loop_count,
            "verification": dict(self.verification),
            "changed_files": list(self.changed_files),
            "termination_reason_detail": list(self.errors),
            "duration_ms": self.duration_ms,
        }
        if include_events:
            data["events"] = [event.to_dict() for event in self.events]
        return data


def request_needs_plan(user_request: str) -> bool:
    """Skip the explicit plan phase only for bounded read-only inspection intents."""
    normalized = " ".join(str(user_request).lower().split())
    trivial_prefixes = (
        "read ", "show ", "list ", "inspect ", "check git status", "git status",
        "what is in ", "find ", "search ",
    )
    return not any(normalized.startswith(prefix) for prefix in trivial_prefixes)
