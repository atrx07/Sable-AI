"""Execution backend selection and public contracts."""

from __future__ import annotations

from collections.abc import Iterable

from .base import (
    BackendAvailability,
    BackendGuarantees,
    BackendUnavailableError,
    EnforcementLevel,
    EnvironmentPolicy,
    ExecutionBackend,
    ExecutionRequest,
    ExecutionResult,
)
from .native import NativeExecutionBackend
from .proot import ProotExecutionBackend, is_termux_environment


def select_execution_backend(
    requested: str = "auto",
    *,
    candidates: Iterable[ExecutionBackend] | None = None,
    workspace_root=None,
    proot_rootfs=None,
) -> ExecutionBackend:
    """Select an available backend without weakening an explicit request."""

    normalized = str(requested or "auto").strip().lower()
    available_candidates = list(candidates) if candidates is not None else [
        ProotExecutionBackend(workspace_root or ".", rootfs=proot_rootfs),
        NativeExecutionBackend(workspace_root),
    ]
    by_name = {backend.name: backend for backend in available_candidates}

    if normalized == "auto":
        for backend in available_candidates:
            if backend.availability().available:
                return backend
        reasons = "; ".join(
            f"{backend.name}: {backend.availability().reason or 'unavailable'}"
            for backend in available_candidates
        )
        raise BackendUnavailableError(f"No execution backend is available. {reasons}".strip())

    backend = by_name.get(normalized)
    if backend is None:
        raise BackendUnavailableError(f"Requested execution backend '{normalized}' is not registered.")
    availability = backend.availability()
    if not availability.available:
        raise BackendUnavailableError(
            f"Requested execution backend '{normalized}' is unavailable: "
            f"{availability.reason or 'no reason reported'}"
        )
    return backend


__all__ = [
    "BackendAvailability",
    "BackendGuarantees",
    "BackendUnavailableError",
    "EnforcementLevel",
    "EnvironmentPolicy",
    "ExecutionBackend",
    "ExecutionRequest",
    "ExecutionResult",
    "NativeExecutionBackend",
    "ProotExecutionBackend",
    "is_termux_environment",
    "select_execution_backend",
]
