import tempfile
import unittest
from pathlib import Path

from sable.tools import ToolExecutor, ToolResult
from sable.runtime import RuntimeEventType
from sable.verification import (
    CheckAvailability,
    CheckCategory,
    CheckStatus,
    VerificationBudget,
    VerificationCheck,
    VerificationPlanner,
    VerificationRunner,
    VerificationResult,
    VerificationScope,
    VerificationStatus,
)
from sable.verifier import Verifier


def check(name, category, argv, *, scope=VerificationScope.AFFECTED, required=True, availability=CheckAvailability.AVAILABLE):
    return VerificationCheck.create(
        name,
        category,
        argv,
        scope=scope,
        required=required,
        availability=availability,
        availability_reason="missing in test" if availability == CheckAvailability.UNAVAILABLE else "available",
    )


class VerificationModelPlannerTests(unittest.TestCase):
    def test_plan_is_deterministic_and_cheap_checks_are_ordered_first(self):
        with tempfile.TemporaryDirectory() as root:
            planner = VerificationPlanner(root)
            candidates = [
                check("tests", CheckCategory.UNIT_TEST, ["python", "-m", "unittest"]),
                check("syntax", CheckCategory.SYNTAX, ["python", "-m", "compileall"], scope=VerificationScope.QUICK),
                check("types", CheckCategory.TYPECHECK, ["mypy", "."]),
            ]
            first = planner.plan(["src/a.py"], candidates=candidates)
            second = planner.plan(["src/a.py"], candidates=list(reversed(candidates)))
            self.assertEqual(first.plan_id, second.plan_id)
            self.assertEqual([item.category for item in first.checks], [
                CheckCategory.SYNTAX, CheckCategory.TYPECHECK, CheckCategory.UNIT_TEST,
            ])

    def test_quick_affected_and_full_scope_filter_checks(self):
        with tempfile.TemporaryDirectory() as root:
            planner = VerificationPlanner(root)
            candidates = [
                check("syntax", CheckCategory.SYNTAX, ["python", "syntax.py"], scope=VerificationScope.QUICK),
                check("tests", CheckCategory.UNIT_TEST, ["python", "tests.py"], scope=VerificationScope.AFFECTED),
                check("build", CheckCategory.BUILD, ["python", "build.py"], scope=VerificationScope.FULL),
            ]
            quick = planner.plan(["a.py"], scope="quick", candidates=candidates)
            affected = planner.plan(["a.py"], scope="affected", candidates=candidates)
            full = planner.plan(["a.py"], scope="full", candidates=candidates)
            self.assertEqual(len(quick.checks), 1)
            self.assertEqual(len(affected.checks), 2)
            self.assertEqual(len(full.checks), 3)

    def test_no_changed_files_produces_skipped_plan(self):
        with tempfile.TemporaryDirectory() as root:
            plan = VerificationPlanner(root).plan([], candidates=[
                check("syntax", CheckCategory.SYNTAX, ["python", "-m", "compileall"]),
            ])
            run = VerificationRunner(ToolExecutor(root)).run(plan)
            self.assertEqual(run.overall_status, VerificationStatus.SKIPPED)
            self.assertEqual(run.results, [])

    def test_check_budget_is_explicit_and_cannot_aggregate_as_pass(self):
        with tempfile.TemporaryDirectory() as root:
            planner = VerificationPlanner(root)
            candidates = [
                check(f"check {index}", CheckCategory.SYNTAX, ["python", "--version"], scope=VerificationScope.QUICK)
                for index in range(3)
            ]
            plan = planner.plan(
                ["a.py"],
                scope="quick",
                candidates=candidates,
                budget=VerificationBudget(max_checks=2),
            )
            run = VerificationRunner(ToolExecutor(root)).run(plan)
            self.assertEqual(len(plan.checks), 2)
            self.assertEqual(plan.checks_omitted, 1)
            self.assertEqual(run.overall_status, VerificationStatus.INCOMPLETE)

    def test_plan_and_check_models_are_json_friendly(self):
        with tempfile.TemporaryDirectory() as root:
            plan = VerificationPlanner(root).plan(
                ["a.py"], candidates=[check("syntax", CheckCategory.SYNTAX, ["python", "--version"])]
            )
            serialized = plan.to_dict()
            self.assertEqual(serialized["scope"], "AFFECTED")
            self.assertIsInstance(serialized["checks"][0]["argv"], list)
            self.assertTrue(serialized["plan_id"].startswith("plan-"))

    def test_verification_result_is_a_first_class_public_model(self):
        item = check("syntax", CheckCategory.SYNTAX, ["python", "--version"])
        result = VerificationResult(item, CheckStatus.PASS, ToolResult("run_command", True))
        self.assertEqual(result.to_dict()["status"], "PASS")


class VerificationRunnerTests(unittest.TestCase):
    def test_runner_emits_bounded_redacted_plan_check_and_completion_events(self):
        with tempfile.TemporaryDirectory() as root:
            secret = "gsk_" + ("a" * 26)
            executor = ToolExecutor(root)
            events = []
            executor.set_runtime_event_handler(lambda event_type, metadata: events.append((event_type, metadata)))
            candidate = VerificationCheck.create(
                f"missing {secret}", CheckCategory.LINT, ["missing-lint", secret],
                availability=CheckAvailability.UNAVAILABLE,
            )
            plan = VerificationPlanner(root).plan(["a.py"], candidates=[candidate])

            VerificationRunner(executor).run(plan)

            event_types = [event_type for event_type, _ in events]
            self.assertIn(RuntimeEventType.VERIFICATION_PLAN_CREATED, event_types)
            self.assertIn(RuntimeEventType.VERIFICATION_CHECK_STARTED, event_types)
            self.assertIn(RuntimeEventType.VERIFICATION_CHECK_SKIPPED, event_types)
            self.assertIn(RuntimeEventType.FAILURE_CLASSIFIED, event_types)
            self.assertIn(RuntimeEventType.VERIFICATION_COMPLETED, event_types)
            self.assertNotIn(secret, repr(events))

    def test_required_unavailable_check_is_incomplete(self):
        with tempfile.TemporaryDirectory() as root:
            unavailable = check(
                "missing checker", CheckCategory.LINT, ["missing-checker"],
                availability=CheckAvailability.UNAVAILABLE,
            )
            plan = VerificationPlanner(root).plan(["a.py"], candidates=[unavailable])
            run = VerificationRunner(ToolExecutor(root)).run(plan)
            self.assertEqual(run.results[0].status, CheckStatus.SKIPPED_UNAVAILABLE)
            self.assertEqual(run.overall_status, VerificationStatus.INCOMPLETE)

    def test_optional_unavailable_check_is_pass_with_optional_skips(self):
        with tempfile.TemporaryDirectory() as root:
            candidates = [
                check("python", CheckCategory.SYNTAX, ["python", "--version"], scope=VerificationScope.QUICK),
                check(
                    "optional lint", CheckCategory.LINT, ["missing-lint"],
                    scope=VerificationScope.QUICK, required=False,
                    availability=CheckAvailability.UNAVAILABLE,
                ),
            ]
            plan = VerificationPlanner(root).plan(["a.py"], candidates=candidates)
            run = VerificationRunner(ToolExecutor(root)).run(plan)
            self.assertEqual(run.overall_status, VerificationStatus.PASS_WITH_OPTIONAL_SKIPS)

    def test_syntax_failure_cancels_later_checks_when_fail_fast(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "broken.py").write_text("def broken(:\n", encoding="utf-8")
            Path(root, "tests.py").write_text("print('should not run')\n", encoding="utf-8")
            candidates = [
                check(
                    "syntax", CheckCategory.SYNTAX,
                    ["python", "-m", "py_compile", "broken.py"], scope=VerificationScope.QUICK,
                ),
                check("tests", CheckCategory.UNIT_TEST, ["python", "tests.py"]),
            ]
            plan = VerificationPlanner(root).plan(["broken.py"], candidates=candidates)
            run = VerificationRunner(ToolExecutor(root)).run(plan)
            self.assertEqual(run.overall_status, VerificationStatus.FAIL)
            self.assertEqual([item.status for item in run.results], [CheckStatus.FAIL, CheckStatus.CANCELLED])

    def test_policy_blocked_required_check_aggregates_blocked(self):
        class BlockingExecutor:
            def dispatch(self, *_args, **_kwargs):
                return ToolResult("run_command", False, error="policy", approval_required=True, risk="blocked")

        with tempfile.TemporaryDirectory() as root:
            plan = VerificationPlanner(root).plan(
                ["a.py"], candidates=[check("custom", CheckCategory.CUSTOM, ["git", "status"])]
            )
            run = VerificationRunner(BlockingExecutor()).run(plan)
            self.assertEqual(run.results[0].status, CheckStatus.BLOCKED)
            self.assertEqual(run.overall_status, VerificationStatus.BLOCKED)

    def test_evidence_records_backend_duration_and_exit_state(self):
        with tempfile.TemporaryDirectory() as root:
            plan = VerificationPlanner(root).plan(
                ["a.py"], candidates=[check("python", CheckCategory.SYNTAX, ["python", "--version"])]
            )
            run = VerificationRunner(ToolExecutor(root)).run(plan)
            evidence = run.evidence.to_dict()
            self.assertEqual(evidence["overall_status"], "PASS")
            recorded = evidence["checks"][0]["result"]
            self.assertEqual(recorded["execution"]["backend"], "native")
            self.assertIsInstance(recorded["duration_ms"], int)

    def test_custom_command_remains_a_complete_override(self):
        with tempfile.TemporaryDirectory() as root:
            verifier = Verifier(ToolExecutor(root))
            result = verifier.verify(["a.py"], run_command="python --version")
            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["overall_status"], "PASS")
            self.assertEqual(result["checks"][0].check.category, CheckCategory.CUSTOM)
            self.assertEqual(len(result["checks"]), 1)


if __name__ == "__main__":
    unittest.main()
