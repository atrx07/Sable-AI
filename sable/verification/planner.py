"""Deterministic verification-plan construction."""

from __future__ import annotations

import hashlib
import json
import shlex
import shutil
import time
from pathlib import Path

from ..project import ProjectInspector
from .models import (
    CheckAvailability,
    CheckCategory,
    VerificationBudget,
    VerificationCheck,
    VerificationPlan,
    VerificationScope,
    VerificationSource,
)


CATEGORY_ORDER = {
    CheckCategory.SYNTAX: 10,
    CheckCategory.FORMAT: 20,
    CheckCategory.LINT: 30,
    CheckCategory.STATIC_ANALYSIS: 35,
    CheckCategory.TYPECHECK: 40,
    CheckCategory.UNIT_TEST: 50,
    CheckCategory.INTEGRATION_TEST: 60,
    CheckCategory.BUILD: 70,
    CheckCategory.PACKAGE: 80,
    CheckCategory.CUSTOM: 90,
}
SCOPE_ORDER = {
    VerificationScope.QUICK: 1,
    VerificationScope.AFFECTED: 2,
    VerificationScope.FULL: 3,
}


def _category(name: str, argv: list[str]) -> CheckCategory:
    text = " ".join([name, *argv]).lower()
    if "compileall" in text or "syntax" in text:
        return CheckCategory.SYNTAX
    if "lint" in text or "ruff" in text or "clippy" in text or "vet" in text:
        return CheckCategory.LINT
    if "type" in text or "mypy" in text or "pyright" in text or "tsc" in text:
        return CheckCategory.TYPECHECK
    if "test" in text or "pytest" in text or "unittest" in text:
        return CheckCategory.UNIT_TEST
    if "build" in text or "cargo check" in text:
        return CheckCategory.BUILD
    if "format" in text or "fmt" in text or "black" in text:
        return CheckCategory.FORMAT
    return CheckCategory.STATIC_ANALYSIS


def _minimum_scope(category: CheckCategory) -> VerificationScope:
    if category in {CheckCategory.SYNTAX, CheckCategory.FORMAT, CheckCategory.LINT, CheckCategory.STATIC_ANALYSIS}:
        return VerificationScope.QUICK
    if category in {CheckCategory.TYPECHECK, CheckCategory.UNIT_TEST}:
        return VerificationScope.AFFECTED
    return VerificationScope.FULL


class VerificationPlanner:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def _available(self, executable: str, cwd: str) -> tuple[CheckAvailability, str]:
        candidate = Path(executable)
        if candidate.is_absolute() or "/" in executable or "\\" in executable:
            local = candidate if candidate.is_absolute() else self.root / cwd / candidate
            if local.is_file():
                return CheckAvailability.AVAILABLE, "Local executable exists."
            return CheckAvailability.UNAVAILABLE, f"Executable is unavailable: {executable}"
        if shutil.which(executable):
            return CheckAvailability.AVAILABLE, "Executable found on PATH."
        return CheckAvailability.UNAVAILABLE, f"Executable is unavailable on PATH: {executable}"

    def legacy_candidates(self, changed_files: list[str]) -> list[VerificationCheck]:
        candidates: list[VerificationCheck] = []
        for name, argv in ProjectInspector(self.root).verification_commands():
            category = _category(name, argv)
            availability, availability_reason = self._available(argv[0], ".")
            candidates.append(VerificationCheck.create(
                name,
                category,
                argv,
                language="legacy",
                toolchain=argv[0],
                scope=_minimum_scope(category),
                reason="Selected by existing deterministic project inspection.",
                affected_files=tuple(changed_files[:100]),
                availability=availability,
                availability_reason=availability_reason,
            ))
        return candidates

    def plan(
        self,
        changed_files: list[str],
        *,
        scope: VerificationScope | str = VerificationScope.AFFECTED,
        custom_command: str | None = None,
        budget: VerificationBudget | None = None,
        candidates: list[VerificationCheck] | None = None,
        fail_fast: bool = True,
    ) -> VerificationPlan:
        started = time.monotonic()
        requested_scope = VerificationScope.parse(scope)
        selected_budget = budget or VerificationBudget()
        normalized_files = tuple(sorted(dict.fromkeys(
            str(path).replace("\\", "/") for path in changed_files if str(path).strip()
        ))[:500])
        reasons: list[str] = [f"Requested {requested_scope.value} verification scope."]
        warnings: list[str] = []

        if custom_command is not None:
            planning_error = ""
            try:
                argv = shlex.split(custom_command, posix=True)
            except ValueError as exc:
                argv = []
                planning_error = f"Invalid custom verification command: {exc}"
            if not argv and not planning_error:
                planning_error = "Custom verification command is empty."
            availability, availability_reason = (
                self._available(argv[0], ".") if argv else
                (CheckAvailability.UNAVAILABLE, planning_error)
            )
            pool = [VerificationCheck.create(
                "custom command",
                CheckCategory.CUSTOM,
                argv,
                scope=requested_scope,
                reason="Explicit session /run override replaces repository-discovered checks.",
                source=VerificationSource.CUSTOM,
                affected_files=normalized_files,
                availability=availability,
                availability_reason=availability_reason,
                planning_error=planning_error,
            )]
            reasons.append("Used the explicit custom verification override.")
        else:
            pool = list(candidates) if candidates is not None else self.legacy_candidates(list(normalized_files))

        if not normalized_files and custom_command is None:
            pool = []
            reasons.append("No changed files were supplied.")

        eligible = [
            check for check in pool
            if SCOPE_ORDER[check.scope] <= SCOPE_ORDER[requested_scope]
        ]
        eligible.sort(key=lambda check: (
            CATEGORY_ORDER[check.category], check.cwd, check.name.lower(), check.check_id
        ))
        omitted = max(0, len(eligible) - selected_budget.max_checks)
        if omitted:
            warnings.append(f"Verification check budget omitted {omitted} check(s).")
        selected = tuple(eligible[:selected_budget.max_checks])
        material = {
            "scope": requested_scope.value,
            "changed_files": normalized_files,
            "checks": [check.check_id for check in selected],
            "budget": selected_budget.to_dict(),
            "checks_omitted": omitted,
        }
        digest = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:20]
        return VerificationPlan(
            plan_id=f"plan-{digest}",
            scope=requested_scope,
            changed_files=normalized_files,
            checks=selected,
            selection_reasons=tuple(reasons),
            warnings=tuple(warnings),
            budget=selected_budget,
            fail_fast=bool(fail_fast),
            checks_omitted=omitted,
            planning_duration_ms=max(0, int((time.monotonic() - started) * 1000)),
        )
