"""Sequential, bounded verification execution through M4 tool dispatch."""

from __future__ import annotations

import time

from ..capabilities import ActionSource
from ..config import redact_secrets
from ..tools import ToolExecutor, ToolResult
from .models import (
    CheckAvailability,
    CheckCategory,
    CheckStatus,
    FailureClassification,
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

    @staticmethod
    def _skipped(check: VerificationCheck, status: CheckStatus, message: str) -> VerificationResult:
        return VerificationResult(
            check,
            status,
            ToolResult("run_command", False, error=message),
            FailureClassification.UNKNOWN_FAILURE,
            diagnostic=message,
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

        for check in plan.checks:
            if stop_after_failure:
                results.append(self._skipped(check, CheckStatus.CANCELLED, "Cancelled by fail-fast policy."))
                continue
            remaining = deadline - time.monotonic()
            if remaining < 1:
                budget_exhausted = True
                results.append(self._skipped(check, CheckStatus.CANCELLED, "Verification wall-time budget exhausted."))
                continue
            if check.planning_error:
                result = ToolResult("verify", False, error=check.planning_error)
                results.append(VerificationResult(
                    check,
                    CheckStatus.ERROR,
                    result,
                    FailureClassification.UNKNOWN_FAILURE,
                    diagnostic=check.planning_error,
                ))
                continue
            if check.availability == CheckAvailability.NOT_APPLICABLE:
                results.append(self._skipped(
                    check, CheckStatus.SKIPPED_NOT_APPLICABLE,
                    check.availability_reason or "Check is not applicable.",
                ))
                continue
            if check.availability != CheckAvailability.AVAILABLE:
                results.append(self._skipped(
                    check, CheckStatus.SKIPPED_UNAVAILABLE,
                    check.availability_reason or "Required verification executable is unavailable.",
                ))
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
                classification = FailureClassification.NONE
            elif tool_result.execution.get("timed_out"):
                status = CheckStatus.TIMEOUT
                classification = FailureClassification.UNKNOWN_FAILURE
            elif tool_result.approval_required or tool_result.risk == "blocked":
                status = CheckStatus.BLOCKED
                classification = FailureClassification.UNKNOWN_FAILURE
            else:
                status = CheckStatus.FAIL
                classification = FailureClassification.UNKNOWN_FAILURE
            diagnostic = redact_secrets(tool_result.error or tool_result.output)[:4000]
            results.append(VerificationResult(
                check,
                status,
                tool_result,
                classification,
                diagnostic=diagnostic,
            ))
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
        return VerificationRun(plan, results, overall, summary, duration_ms, budget_exhausted, evidence)
