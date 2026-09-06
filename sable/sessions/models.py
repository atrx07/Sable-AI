"""Persistent session and trace record models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class SessionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


@dataclass
class SessionRecord:
    session_id: str
    created_at: str
    updated_at: str
    workspace: str
    provider: str
    main_model: str
    fast_model: str
    status: str = SessionStatus.ACTIVE.value
    task_ids: list[str] = field(default_factory=list)
    total_model_calls: int = 0
    total_tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    total_duration_ms: int = 0
    latest_task: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SessionRecord":
        if not isinstance(value, dict):
            raise TypeError("session metadata must be an object")
        allowed = cls.__dataclass_fields__
        return cls(**{key: item for key, item in value.items() if key in allowed})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TraceEvent:
    event_id: str
    timestamp: str
    session_id: str
    task_id: str | None
    transaction_id: str | None
    event_type: str
    phase: str | None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TraceEvent":
        if not isinstance(value, dict):
            raise TypeError("trace event must be an object")
        return cls(
            event_id=str(value.get("event_id", "")),
            timestamp=str(value.get("timestamp", "")),
            session_id=str(value.get("session_id", "")),
            task_id=str(value["task_id"]) if value.get("task_id") else None,
            transaction_id=str(value["transaction_id"]) if value.get("transaction_id") else None,
            event_type=str(value.get("event_type", "")),
            phase=str(value["phase"]) if value.get("phase") else None,
            metadata=value.get("metadata", {}) if isinstance(value.get("metadata"), dict) else {},
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
