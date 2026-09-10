"""Deterministic verification-plan construction."""

from __future__ import annotations

import hashlib
import json
import shlex
import time
from pathlib import Path

from ..context import ContextEngine
from ..config import redact_secrets
from .affected import AffectedTestSelector, is_verification_config
from .adapters import AvailabilityResolver
from .discovery import DiscoveryResult, VerificationDiscovery
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


class VerificationPlanner:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.availability = AvailabilityResolver(self.root)
        self.discovery = VerificationDiscovery(self.root)
        self.affected_selector = AffectedTestSelector(self.root)
        self.last_discovery: DiscoveryResult | None = None

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
        manifests: tuple[str, ...] = ()
        project_roots: tuple[str, ...] = ()
        adapters: tuple[str, ...] = ()
        roots_avoided = 0
        affected_test_count = 0
        checks_avoided = 0
        effective_scope = requested_scope

        config_changes = tuple(path for path in normalized_files if is_verification_config(path))
        if custom_command is None and config_changes and requested_scope != VerificationScope.FULL:
            effective_scope = VerificationScope.FULL
            reasons.append(
                "Escalated to FULL because verification/build configuration changed: "
                + ", ".join(config_changes[:8]) + "."
            )

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
                self.availability.executable(argv[0], ".") if argv else
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
            if candidates is not None:
                pool = list(candidates)
                self.last_discovery = None
            else:
                self.last_discovery = self.discovery.discover(normalized_files, scope=effective_scope)
                pool = list(self.last_discovery.checks)
                manifests = self.last_discovery.manifests
                project_roots = self.last_discovery.project_roots
                adapters = self.last_discovery.adapters
                roots_avoided = self.last_discovery.roots_avoided
                warnings.extend(self.last_discovery.warnings)
                if adapters:
                    reasons.append("Discovered manifest-configured checks through: " + ", ".join(adapters) + ".")
                if roots_avoided:
                    reasons.append(f"Avoided {roots_avoided} unrelated project root(s) for this change set.")
                if effective_scope in {VerificationScope.QUICK, VerificationScope.AFFECTED} and pool:
                    try:
                        context = ContextEngine(self.root).build()
                        pool, affected = self.affected_selector.refine_checks(
                            pool, normalized_files, context, effective_scope
                        )
                        affected_test_count = len(affected.targets)
                        checks_avoided = affected.candidates_avoided
                        if affected.targets:
                            reasons.append(
                                f"Selected {len(affected.targets)} affected test target(s) using bounded Context Engine relationships."
                            )
                        if affected.central_files:
                            reasons.append(
                                "Expanded affected analysis for high-fanout file(s): "
                                + ", ".join(affected.central_files[:8]) + "."
                            )
                    except (OSError, RuntimeError, ValueError) as exc:
                        warnings.append(
                            "Affected-target analysis was unavailable; using project-level checks: "
                            + redact_secrets(str(exc))[:300]
                        )

        if not normalized_files and custom_command is None:
            pool = []
            reasons.append("No changed files were supplied.")

        eligible = [
            check for check in pool
            if SCOPE_ORDER[check.scope] <= SCOPE_ORDER[effective_scope]
        ]
        eligible.sort(key=lambda check: (
            CATEGORY_ORDER[check.category], check.cwd, check.name.lower(), check.check_id
        ))
        omitted = max(0, len(eligible) - selected_budget.max_checks)
        if omitted:
            warnings.append(f"Verification check budget omitted {omitted} check(s).")
        selected = tuple(eligible[:selected_budget.max_checks])
        material = {
            "scope": effective_scope.value,
            "requested_scope": requested_scope.value,
            "changed_files": normalized_files,
            "checks": [check.check_id for check in selected],
            "budget": selected_budget.to_dict(),
            "checks_omitted": omitted,
            "manifests": manifests,
            "project_roots": project_roots,
        }
        digest = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:20]
        return VerificationPlan(
            plan_id=f"plan-{digest}",
            scope=effective_scope,
            changed_files=normalized_files,
            checks=selected,
            selection_reasons=tuple(reasons),
            warnings=tuple(warnings),
            budget=selected_budget,
            requested_scope=requested_scope,
            discovered_manifests=manifests,
            project_roots=project_roots,
            adapters=adapters,
            roots_avoided=roots_avoided,
            affected_test_count=affected_test_count,
            checks_avoided=checks_avoided,
            fail_fast=bool(fail_fast),
            checks_omitted=omitted,
            planning_duration_ms=max(0, int((time.monotonic() - started) * 1000)),
        )
