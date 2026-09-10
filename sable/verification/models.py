"""Typed, JSON-friendly models for deterministic verification."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from ..config import redact_secrets
from ..tools.base import ToolResult


EVIDENCE_TEXT_LIMIT = 4000


def _bounded_text(value: object, limit: int = EVIDENCE_TEXT_LIMIT) -> tuple[str, bool]:
    text = redact_secrets(str(value or ""))
    if len(text) <= limit:
        return text, False
    marker = "\n... [verification evidence truncated] ...\n"
    remaining = max(0, limit - len(marker))
    left = remaining // 2
    right = remaining - left
    return text[:left] + marker + (text[-right:] if right else ""), True


def _public_tool_result(result: ToolResult) -> dict[str, Any]:
    """Serialize bounded evidence without persisting volatile approval identifiers."""
    output, output_truncated = _bounded_text(result.output)
    error, error_truncated = _bounded_text(result.error)
    security = {
        key: value
        for key, value in result.security.items()
        if key in {"source", "allowed", "allowed_by", "required_capabilities"}
    }
    authorizations = []
    for item in result.security.get("authorizations", []):
        if not isinstance(item, dict):
            continue
        authorizations.append({
            key: item.get(key)
            for key in ("allowed", "capability", "allowed_by", "approval_required")
            if key in item
        })
    if authorizations:
        security["authorizations"] = authorizations
    return {
        "tool": result.tool,
        "success": result.success,
        "output": output,
        "error": error,
        "exit_code": result.exit_code,
        "duration_ms": result.duration_ms,
        "changed_files": [redact_secrets(path)[:500] for path in result.changed_files[:500]],
        "truncated": result.truncated or output_truncated or error_truncated,
        "approval_required": result.approval_required,
        "risk": result.risk,
        "execution": dict(result.execution),
        "security": security,
    }


class VerificationScope(str, Enum):
    QUICK = "QUICK"
    AFFECTED = "AFFECTED"
    FULL = "FULL"

    @classmethod
    def parse(cls, value: "VerificationScope | str") -> "VerificationScope":
        if isinstance(value, cls):
            return value
        return cls(str(value or cls.AFFECTED.value).strip().upper())


class CheckCategory(str, Enum):
    SYNTAX = "SYNTAX"
    FORMAT = "FORMAT"
    LINT = "LINT"
    TYPECHECK = "TYPECHECK"
    UNIT_TEST = "UNIT_TEST"
    INTEGRATION_TEST = "INTEGRATION_TEST"
    BUILD = "BUILD"
    PACKAGE = "PACKAGE"
    STATIC_ANALYSIS = "STATIC_ANALYSIS"
    CUSTOM = "CUSTOM"


class CheckAvailability(str, Enum):
    CONFIGURED = "CONFIGURED"
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIPPED_NOT_APPLICABLE = "SKIPPED_NOT_APPLICABLE"
    SKIPPED_UNAVAILABLE = "SKIPPED_UNAVAILABLE"
    SKIPPED_POLICY = "SKIPPED_POLICY"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


class VerificationStatus(str, Enum):
    PASS = "PASS"
    PASS_WITH_OPTIONAL_SKIPS = "PASS_WITH_OPTIONAL_SKIPS"
    FAIL = "FAIL"
    INCOMPLETE = "INCOMPLETE"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"


class VerificationSource(str, Enum):
    DISCOVERED = "DISCOVERED"
    CUSTOM = "CUSTOM"
    RUNTIME = "RUNTIME"


class FailureClassification(str, Enum):
    NONE = "NONE"
    SYNTAX_ERROR = "SYNTAX_ERROR"
    IMPORT_ERROR = "IMPORT_ERROR"
    TYPE_ERROR = "TYPE_ERROR"
    LINT_ERROR = "LINT_ERROR"
    ASSERTION_FAILURE = "ASSERTION_FAILURE"
    TEST_COLLECTION_FAILURE = "TEST_COLLECTION_FAILURE"
    BUILD_ERROR = "BUILD_ERROR"
    DEPENDENCY_MISSING = "DEPENDENCY_MISSING"
    TOOL_MISSING = "TOOL_MISSING"
    TIMEOUT = "TIMEOUT"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    RESOURCE_LIMIT = "RESOURCE_LIMIT"
    UNKNOWN_FAILURE = "UNKNOWN_FAILURE"


@dataclass(frozen=True)
class VerificationBudget:
    max_checks: int = 8
    total_timeout_seconds: int = 300
    per_check_timeout_seconds: int = 120
    max_repair_cycles: int = 2

    def __post_init__(self) -> None:
        if self.max_checks < 1 or self.total_timeout_seconds < 1 or self.per_check_timeout_seconds < 1 or self.max_repair_cycles < 0:
            raise ValueError("Verification budgets must be positive.")

    def to_dict(self) -> dict[str, int]:
        return {
            "max_checks": self.max_checks,
            "total_timeout_seconds": self.total_timeout_seconds,
            "per_check_timeout_seconds": self.per_check_timeout_seconds,
            "max_repair_cycles": self.max_repair_cycles,
        }


@dataclass(frozen=True)
class VerificationCheck:
    check_id: str
    name: str
    category: CheckCategory
    argv: tuple[str, ...]
    cwd: str = "."
    language: str = "unknown"
    toolchain: str = "unknown"
    scope: VerificationScope = VerificationScope.AFFECTED
    reason: str = ""
    source: VerificationSource = VerificationSource.DISCOVERED
    affected_files: tuple[str, ...] = ()
    timeout_seconds: int = 120
    required: bool = True
    availability: CheckAvailability = CheckAvailability.AVAILABLE
    availability_reason: str = ""
    dependencies: tuple[str, ...] = ()
    expected_evidence: str = "exit_code"
    planning_error: str = ""
    target_reasons: tuple[str, ...] = ()

    @classmethod
    def create(
        cls,
        name: str,
        category: CheckCategory,
        argv: list[str] | tuple[str, ...],
        **kwargs: Any,
    ) -> "VerificationCheck":
        normalized = dict(kwargs)
        normalized_scope = VerificationScope.parse(normalized.get("scope", VerificationScope.AFFECTED))
        normalized["scope"] = normalized_scope
        normalized_availability = normalized.get("availability", CheckAvailability.AVAILABLE)
        if not isinstance(normalized_availability, CheckAvailability):
            normalized_availability = CheckAvailability(str(normalized_availability).upper())
        normalized["availability"] = normalized_availability
        material = {
            "name": str(name),
            "category": category.value,
            "argv": list(argv),
            "cwd": str(kwargs.get("cwd", ".")),
            "language": str(kwargs.get("language", "unknown")),
            "scope": normalized_scope.value,
            "toolchain": str(normalized.get("toolchain", "unknown")),
            "required": bool(normalized.get("required", True)),
            "availability": normalized_availability.value,
            "dependencies": list(normalized.get("dependencies", ())),
        }
        digest = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]
        return cls(
            check_id=f"check-{digest}",
            name=redact_secrets(str(name))[:160],
            category=category,
            argv=tuple(str(item) for item in argv),
            **normalized,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "name": self.name,
            "category": self.category.value,
            "argv": [redact_secrets(item)[:500] for item in self.argv],
            "cwd": self.cwd,
            "language": self.language,
            "toolchain": self.toolchain,
            "scope": self.scope.value,
            "reason": redact_secrets(self.reason)[:500],
            "source": self.source.value,
            "affected_files": list(self.affected_files),
            "timeout_seconds": self.timeout_seconds,
            "required": self.required,
            "availability": self.availability.value,
            "availability_reason": redact_secrets(self.availability_reason)[:300],
            "dependencies": list(self.dependencies),
            "expected_evidence": self.expected_evidence,
            "planning_error": redact_secrets(self.planning_error)[:300],
            "target_reasons": [redact_secrets(reason)[:500] for reason in self.target_reasons[:100]],
        }

    def with_updates(self, **changes: Any) -> "VerificationCheck":
        values = {
            "cwd": self.cwd,
            "language": self.language,
            "toolchain": self.toolchain,
            "scope": self.scope,
            "reason": self.reason,
            "source": self.source,
            "affected_files": self.affected_files,
            "timeout_seconds": self.timeout_seconds,
            "required": self.required,
            "availability": self.availability,
            "availability_reason": self.availability_reason,
            "dependencies": self.dependencies,
            "expected_evidence": self.expected_evidence,
            "planning_error": self.planning_error,
            "target_reasons": self.target_reasons,
        }
        name = str(changes.pop("name", self.name))
        category = changes.pop("category", self.category)
        argv = changes.pop("argv", self.argv)
        values.update(changes)
        return VerificationCheck.create(name, category, argv, **values)


@dataclass(frozen=True)
class VerificationPlan:
    plan_id: str
    scope: VerificationScope
    changed_files: tuple[str, ...]
    checks: tuple[VerificationCheck, ...]
    selection_reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    budget: VerificationBudget
    requested_scope: VerificationScope | None = None
    discovered_manifests: tuple[str, ...] = ()
    project_roots: tuple[str, ...] = ()
    adapters: tuple[str, ...] = ()
    roots_avoided: int = 0
    affected_test_count: int = 0
    checks_avoided: int = 0
    fail_fast: bool = True
    checks_omitted: int = 0
    planning_duration_ms: int = 0

    @property
    def budget_limited(self) -> bool:
        return self.checks_omitted > 0

    @property
    def scope_escalated(self) -> bool:
        return self.requested_scope is not None and self.requested_scope != self.scope

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "scope": self.scope.value,
            "requested_scope": (self.requested_scope or self.scope).value,
            "scope_escalated": self.scope_escalated,
            "changed_files": list(self.changed_files),
            "checks": [check.to_dict() for check in self.checks],
            "selection_reasons": list(self.selection_reasons),
            "warnings": list(self.warnings),
            "budget": self.budget.to_dict(),
            "discovered_manifests": list(self.discovered_manifests),
            "project_roots": list(self.project_roots),
            "adapters": list(self.adapters),
            "roots_avoided": self.roots_avoided,
            "affected_test_count": self.affected_test_count,
            "checks_avoided": self.checks_avoided,
            "fail_fast": self.fail_fast,
            "checks_omitted": self.checks_omitted,
            "budget_limited": self.budget_limited,
            "planning_duration_ms": self.planning_duration_ms,
        }


@dataclass
class VerificationResult:
    check: VerificationCheck
    status: CheckStatus
    result: ToolResult
    classification: FailureClassification = FailureClassification.NONE
    diagnostic: str = ""
    failure_signature: str = ""

    @property
    def name(self) -> str:
        return self.check.name

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check.to_dict(),
            "status": self.status.value,
            "classification": self.classification.value,
            "diagnostic": redact_secrets(self.diagnostic)[:4000],
            "failure_signature": self.failure_signature,
            "result": _public_tool_result(self.result),
        }


# Backward-compatible descriptive alias for early M5 integrations.
VerificationCheckResult = VerificationResult


@dataclass(frozen=True)
class VerificationEvidence:
    evidence_id: str
    created_at: str
    plan_id: str
    scope: VerificationScope
    overall_status: VerificationStatus
    checks: tuple[dict[str, Any], ...]
    duration_ms: int
    budget_exhausted: bool

    @classmethod
    def create(
        cls,
        plan: VerificationPlan,
        status: VerificationStatus,
        results: list[VerificationResult],
        duration_ms: int,
        budget_exhausted: bool,
    ) -> "VerificationEvidence":
        return cls(
            evidence_id=f"evidence-{uuid.uuid4().hex}",
            created_at=datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            plan_id=plan.plan_id,
            scope=plan.scope,
            overall_status=status,
            checks=tuple(item.to_dict() for item in results),
            duration_ms=duration_ms,
            budget_exhausted=budget_exhausted,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "created_at": self.created_at,
            "plan_id": self.plan_id,
            "scope": self.scope.value,
            "overall_status": self.overall_status.value,
            "checks": list(self.checks),
            "duration_ms": self.duration_ms,
            "budget_exhausted": self.budget_exhausted,
        }


@dataclass
class VerificationRun:
    plan: VerificationPlan
    results: list[VerificationResult]
    overall_status: VerificationStatus
    summary: str
    duration_ms: int
    budget_exhausted: bool = False
    evidence: VerificationEvidence | None = None

    def to_result_dict(self) -> dict[str, Any]:
        legacy_status = (
            "pass"
            if self.overall_status in {VerificationStatus.PASS, VerificationStatus.PASS_WITH_OPTIONAL_SKIPS}
            else "skipped" if self.overall_status == VerificationStatus.SKIPPED
            else "fail"
        )
        return {
            "status": legacy_status,
            "overall_status": self.overall_status.value,
            "scope": self.plan.scope.value,
            "summary": self.summary,
            "checks": self.results,
            "plan": self.plan.to_dict(),
            "evidence": self.evidence.to_dict() if self.evidence else {},
            "duration_ms": self.duration_ms,
            "budget_exhausted": self.budget_exhausted,
        }
