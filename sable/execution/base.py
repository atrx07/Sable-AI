"""Provider-neutral subprocess execution contracts and guarantee metadata."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Mapping, Sequence


class EnforcementLevel(str, Enum):
    """How strongly an execution backend implements one isolation property."""

    ENFORCED = "ENFORCED"
    BEST_EFFORT = "BEST_EFFORT"
    NOT_SUPPORTED = "NOT_SUPPORTED"


class EnvironmentPolicy(str, Enum):
    PROJECT = "PROJECT"
    AMBIENT = "AMBIENT"


@dataclass(frozen=True)
class BackendGuarantees:
    workspace_path_confinement: EnforcementLevel
    private_home: EnforcementLevel
    sanitized_environment: EnforcementLevel
    filesystem_namespace: EnforcementLevel
    network_isolation: EnforcementLevel
    process_isolation: EnforcementLevel
    resource_limits: EnforcementLevel
    descendant_cleanup: EnforcementLevel
    shell_disabled_by_default: EnforcementLevel

    def to_dict(self) -> dict[str, str]:
        return {name: value.value for name, value in asdict(self).items()}


@dataclass(frozen=True)
class BackendAvailability:
    available: bool
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {"available": self.available, "reason": self.reason}


@dataclass(frozen=True)
class ExecutionRequest:
    argv: Sequence[str] | str
    cwd: Path
    timeout_seconds: int
    shell: bool = False
    env: Mapping[str, str] | None = None
    environment_policy: EnvironmentPolicy = EnvironmentPolicy.PROJECT
    max_output_chars: int = 12000

    def __post_init__(self) -> None:
        if self.shell:
            if not isinstance(self.argv, str) or not self.argv.strip():
                raise ValueError("Shell execution requires a non-empty command string.")
        elif (
            isinstance(self.argv, str)
            or not self.argv
            or not all(isinstance(item, str) and item for item in self.argv)
        ):
            raise ValueError("Direct execution requires a non-empty argv string sequence.")
        if int(self.timeout_seconds) < 1:
            raise ValueError("Execution timeout must be at least one second.")
        if int(self.max_output_chars) < 256:
            raise ValueError("Execution output limit must be at least 256 characters.")


@dataclass(frozen=True)
class ExecutionResult:
    backend: str
    success: bool
    output: str = ""
    error: str = ""
    exit_code: int | None = None
    duration_ms: int = 0
    timed_out: bool = False
    terminated: bool = False
    truncated: bool = False
    guarantees: BackendGuarantees | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    def execution_metadata(self) -> dict[str, object]:
        return {
            "backend": self.backend,
            "timed_out": self.timed_out,
            "terminated": self.terminated,
            "guarantees": self.guarantees.to_dict() if self.guarantees else {},
            **dict(self.metadata),
        }


class BackendUnavailableError(RuntimeError):
    pass


class ExecutionBackend(ABC):
    """Runtime-owned boundary for launching and normalizing subprocesses."""

    name: str
    guarantees: BackendGuarantees

    @abstractmethod
    def availability(self) -> BackendAvailability:
        raise NotImplementedError

    @abstractmethod
    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        raise NotImplementedError

    def status(self) -> dict[str, object]:
        availability = self.availability()
        return {
            "name": self.name,
            **availability.to_dict(),
            "guarantees": self.guarantees.to_dict(),
        }


def bounded_output(text: str, limit: int) -> tuple[str, bool]:
    value = str(text or "")
    if len(value) <= limit:
        return value, False
    omitted = len(value) - limit
    marker = f"\n... [{omitted} chars omitted] ...\n"
    remaining = max(0, limit - len(marker))
    left = remaining // 2
    right = remaining - left
    return value[:left] + marker + (value[-right:] if right else ""), True
