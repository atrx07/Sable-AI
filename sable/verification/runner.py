"""Sequential, bounded verification execution through M4 tool dispatch."""

from __future__ import annotations

import time

from ..capabilities import ActionSource
from ..config import redact_secrets
from ..runtime import RuntimeEventType
from ..tools import ToolExecutor, ToolResult
from .classifiers import FailureClassifier
from .models import (
    CheckAvailability,
    CheckCategory,
    CheckStatus,
    VerificationCheck,
    VerificationResult,
    VerificationEvidence,
    VerificationPlan,
    VerificationRun,
    VerificationStatus,
)


class VerificationRunner:
    def __init__(self, executor: ToolExecutor):
        self.executor = executor
        self.classifier = FailureClassifier()

    def _emit(self, event_type: RuntimeEventType, **metadata) -> None:
        emitter = getattr(self.executor, "_emit_runtime_event", None)
        if emitter is not None:
            emitter(event_type, **metadata)

    def _record_result(self, item: VerificationResult) -> None:
        if item.status == CheckStatus.PASS:
            event_type = RuntimeEventType.VERIFICATION_CHECK_PASSED
        elif item.status in {
            CheckStatus.SKIPPED_NOT_APPLICABLE,
            CheckStatus.SKIPPED_UNAVAILABLE,
            CheckStatus.SKIPPED_POLICY,
            CheckStatus.CANCELLED,
        }:
            event_type = RuntimeEventType.VERIFICATION_CHECK_SKIPPED
        else:
            event_type = RuntimeEventType.VERIFICATION_CHECK_FAILED
        self._emit(
            event_type,
            check_id=item.check.check_id,
            name=item.check.name,
            status=item.status.value,
            classification=item.classification.value,
            duration_ms=item.result.duration_ms,
            exit_code=item.result.exit_code,
        )
        if item.classification.value not in {"NONE", "UNKNOWN_FAILURE"}:
            self._emit(
                RuntimeEventType.FAILURE_CLASSIFIED,
                check_id=item.check.check_id,
                classification=item.classification.value,
                failure_signature=item.failure_signature,
            )

    def _skipped(self, check: VerificationCheck, status: CheckStatus, message: str) -> VerificationResult:
        tool_result = ToolResult("run_command", False, error=message)
        classification, diagnostic, signature = self.classifier.analyze(check, status, tool_result)
        return VerificationResult(
            check,
            status,
            tool_result,
            classification,
            diagnostic=diagnostic,
            failure_signature=signature,
        )

    @staticmethod
    def aggregate(
        plan: VerificationPlan,
        results: list[VerificationResult],
        *,
        budget_exhausted: bool,
    ) -> VerificationStatus:
        if not plan.checks:
            return VerificationStatus.SKIPPED
        required = [item for item in results if item.check.required]
        if any(item.status == CheckStatus.FAIL for item in required):
            return VerificationStatus.FAIL
        if any(item.status in {CheckStatus.BLOCKED, CheckStatus.SKIPPED_POLICY} for item in required):
            return VerificationStatus.BLOCKED
        incomplete = {
            CheckStatus.SKIPPED_UNAVAILABLE,
            CheckStatus.SKIPPED_NOT_APPLICABLE,
            CheckStatus.TIMEOUT,
            CheckStatus.ERROR,
        }
        if budget_exhausted or plan.budget_limited or any(item.status in incomplete for item in required):
            return VerificationStatus.INCOMPLETE
        optional_skips = any(
            not item.check.required and item.status != CheckStatus.PASS
            for item in results
        )
        return VerificationStatus.PASS_WITH_OPTIONAL_SKIPS if optional_skips else VerificationStatus.PASS

    def run(self, plan: VerificationPlan, *, mode: str = "build") -> VerificationRun:
        started = time.monotonic()
        deadline = started + plan.budget.total_timeout_seconds
        results: list[VerificationResult] = []
        budget_exhausted = False
        stop_after_failure = False

        self._emit(
            RuntimeEventType.VERIFICATION_PLAN_CREATED,
            plan_id=plan.plan_id,
            scope=plan.scope.value,
            requested_scope=(plan.requested_scope or plan.scope).value,
            check_count=len(plan.checks),
            checks_omitted=plan.checks_omitted,
            affected_test_count=plan.affected_test_count,
        )
        if plan.scope_escalated:
            self._emit(
                RuntimeEventType.VERIFICATION_SCOPE_ESCALATED,
                requested_scope=(plan.requested_scope or plan.scope).value,
                effective_scope=plan.scope.value,
            )

        for check in plan.checks:
            self._emit(
                RuntimeEventType.VERIFICATION_CHECK_STARTED,
                plan_id=plan.plan_id,
                check_id=check.check_id,
                name=redact_secrets(check.name)[:160],
                category=check.category.value,
                argv=[redact_secrets(item)[:500] for item in check.argv[:50]],
                cwd=redact_secrets(check.cwd)[:500],
            )
            if stop_after_failure:
                item = self._skipped(check, CheckStatus.CANCELLED, "Cancelled by fail-fast policy.")
                results.append(item)
                self._record_result(item)
                continue
            remaining = deadline - time.monotonic()
            if remaining < 1:
                budget_exhausted = True
                item = self._skipped(check, CheckStatus.CANCELLED, "Verification wall-time budget exhausted.")
                results.append(item)
                self._record_result(item)
                continue
            if check.planning_error:
                result = ToolResult("verify", False, error=check.planning_error)
                classification, diagnostic, signature = self.classifier.analyze(check, CheckStatus.ERROR, result)
                item = VerificationResult(
                    check,
                    CheckStatus.ERROR,
                    result,
                    classification,
                    diagnostic=diagnostic,
                    failure_signature=signature,
                )
                results.append(item)
                self._record_result(item)
                continue
            if check.availability == CheckAvailability.NOT_APPLICABLE:
                item = self._skipped(
                    check, CheckStatus.SKIPPED_NOT_APPLICABLE,
                    check.availability_reason or "Check is not applicable.",
                )
                results.append(item)
                self._record_result(item)
                continue
            if check.availability != CheckAvailability.AVAILABLE:
                item = self._skipped(
                    check, CheckStatus.SKIPPED_UNAVAILABLE,
                    check.availability_reason or "Required verification executable is unavailable.",
                )
                results.append(item)
                self._record_result(item)
                continue

            timeout = max(1, min(
                check.timeout_seconds,
                plan.budget.per_check_timeout_seconds,
                int(remaining),
            ))
            tool_result = self.executor.dispatch(
                "run_command",
                {"argv": list(check.argv), "cwd": check.cwd, "timeout": timeout},
                mode=mode,
                source=ActionSource.VERIFIER,
            )
            if tool_result.success:
                status = CheckStatus.PASS
            elif tool_result.execution.get("timed_out"):
                status = CheckStatus.TIMEOUT
            elif tool_result.approval_required or tool_result.risk == "blocked":
                status = CheckStatus.BLOCKED
            else:
                status = CheckStatus.FAIL
            classification, diagnostic, signature = self.classifier.analyze(check, status, tool_result)
            item = VerificationResult(
                check,
                status,
                tool_result,
                classification,
                diagnostic=diagnostic,
                failure_signature=signature,
            )
            results.append(item)
            self._record_result(item)
            if plan.fail_fast and check.required and status in {
                CheckStatus.FAIL, CheckStatus.TIMEOUT, CheckStatus.BLOCKED, CheckStatus.ERROR,
            } and check.category == CheckCategory.SYNTAX:
                stop_after_failure = True

        duration_ms = max(0, int((time.monotonic() - started) * 1000))
        overall = self.aggregate(plan, results, budget_exhausted=budget_exhausted)
        counts: dict[str, int] = {}
        for item in results:
            counts[item.status.value] = counts.get(item.status.value, 0) + 1
        detail = ", ".join(f"{name}={count}" for name, count in sorted(counts.items())) or "no checks"
        summary = f"{overall.value}: {detail}."
        evidence = VerificationEvidence.create(plan, overall, results, duration_ms, budget_exhausted)
        if budget_exhausted or plan.budget_limited:
            self._emit(
                RuntimeEventType.VERIFICATION_BUDGET_EXHAUSTED,
                plan_id=plan.plan_id,
                wall_time_exhausted=budget_exhausted,
                checks_omitted=plan.checks_omitted,
            )
        self._emit(
            RuntimeEventType.VERIFICATION_COMPLETED,
            plan_id=plan.plan_id,
            status=overall.value,
            duration_ms=duration_ms,
            evidence_id=evidence.evidence_id,
        )
        return VerificationRun(plan, results, overall, summary, duration_ms, budget_exhausted, evidence)
