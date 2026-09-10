"""Compatibility facade for the structured deterministic verification engine."""

from __future__ import annotations

from pathlib import Path

from .tools import ToolExecutor
from .verification import (
    VerificationBudget,
    VerificationCheck,
    VerificationPlanner,
    VerificationRunner,
    VerificationScope,
    is_verification_config,
)


CODE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".go", ".rs",
    ".c", ".h", ".cpp", ".cc", ".cs", ".php", ".rb", ".sh", ".bash",
}


class Verifier:
    """Plan and run verification while preserving the established public API."""

    def __init__(
        self,
        executor: ToolExecutor,
        *,
        default_scope: VerificationScope | str = VerificationScope.AFFECTED,
        budget: VerificationBudget | None = None,
    ):
        self.executor = executor
        self.default_scope = VerificationScope.parse(default_scope)
        self.budget = budget or VerificationBudget()
        self.planner = VerificationPlanner(executor.project_dir)
        self.runner = VerificationRunner(executor)
        self.last_run = None

    @staticmethod
    def needs_verification(changed_files: list[str]) -> bool:
        return any(
            Path(path).suffix.lower() in CODE_EXTENSIONS or is_verification_config(path)
            for path in changed_files
        )

    def verify(
        self,
        changed_files: list[str],
        run_command: str | None = None,
        *,
        mode: str = "build",
        scope: VerificationScope | str | None = None,
    ) -> dict:
        if not self.needs_verification(changed_files) and run_command is None:
            plan = self.planner.plan(
                [],
                scope=scope or self.default_scope,
                budget=self.budget,
            )
        else:
            plan = self.planner.plan(
                changed_files,
                scope=scope or self.default_scope,
                custom_command=run_command,
                budget=self.budget,
            )
        self.last_run = self.runner.run(plan, mode=mode)
        return self.last_run.to_result_dict()


__all__ = ["VerificationCheck", "Verifier"]
