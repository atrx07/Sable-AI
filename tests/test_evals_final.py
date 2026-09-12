import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from sable.evals import (
    EvalDisposition,
    EvalMode,
    EvalResult,
    EvaluationRunner,
    SystemScenarioExecutor,
    aggregate_metrics,
    build_report,
    compare_baseline,
    load_baseline,
    load_scenario_suite,
    write_report,
)
from sable.evals.cli import main


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evals" / "fixtures"
CATALOGS = (
    ROOT / "evals" / "scenarios" / "m7.2-system.json",
    ROOT / "evals" / "scenarios" / "m7.3-adversarial.json",
    ROOT / "evals" / "scenarios" / "m7.5-resilience.json",
)
BASELINE = ROOT / "evals" / "baselines" / "m7-deterministic.json"


def synthetic_result(scenario, *, disposition=EvalDisposition.PASS):
    context = {}
    if scenario.required_context_files:
        context = {
            "required_context_files": len(scenario.required_context_files),
            "fixture_context_recall": 1.0,
            "fixture_context_precision": 1.0,
        }
    return EvalResult(
        scenario_id=scenario.scenario_id,
        category=scenario.category,
        mode=EvalMode.DETERMINISTIC,
        expected_outcome=scenario.expected_outcome,
        actual_outcome=scenario.expected_outcome if disposition == EvalDisposition.PASS else None,
        disposition=disposition,
        started_at="2026-01-01T00:00:00+00:00",
        duration_ms=1,
        verified=True,
        verification_status="PASS",
        context_metrics=context,
    )


class FinalEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenarios = [item for path in CATALOGS for item in load_scenario_suite(path)]

    def test_resilience_catalog_is_bounded_offline_and_green(self):
        scenarios = load_scenario_suite(CATALOGS[-1])
        self.assertEqual(len(scenarios), 8)
        self.assertTrue(all("ci" in item.tags for item in scenarios))
        serialized = json.dumps([item.to_dict() for item in scenarios]).lower()
        self.assertNotIn("http://", serialized)
        self.assertNotIn("https://", serialized)
        self.assertNotIn("groq", serialized)
        results = EvaluationRunner(FIXTURES).run_many(scenarios, SystemScenarioExecutor())
        failures = [item.to_dict() for item in results if not item.passed]
        self.assertEqual(failures, [])

    def test_canonical_baseline_matches_complete_catalog_and_stable_metrics(self):
        baseline = load_baseline(BASELINE)
        self.assertEqual(set(baseline["scenario_ids"]), {item.scenario_id for item in self.scenarios})
        report = build_report(
            [synthetic_result(item) for item in self.scenarios],
            mode=EvalMode.DETERMINISTIC,
            repository_root=ROOT,
        )
        comparison = compare_baseline(report, baseline)
        self.assertTrue(comparison.passed, comparison.errors)

    def test_baseline_rejects_failure_missing_case_and_hidden_skip(self):
        baseline = load_baseline(BASELINE)
        results = [synthetic_result(item) for item in self.scenarios]
        results[0].disposition = EvalDisposition.FAIL
        results[1].disposition = EvalDisposition.SKIPPED_NOT_REQUESTED
        report = build_report(results[:-1], mode=EvalMode.DETERMINISTIC, repository_root=ROOT)
        comparison = compare_baseline(report, baseline)
        self.assertFalse(comparison.passed)
        detail = "\n".join(comparison.errors)
        self.assertIn("failed scenarios", detail)
        self.assertIn("missing scenarios", detail)
        self.assertIn("non-platform skips", detail)

    def test_baseline_loader_rejects_malformed_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "baseline.json")
            path.write_text('{"schema_version": 1}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "eval_schema_version"):
                load_baseline(path)

    def test_skip_accounting_never_counts_a_skip_as_a_pass(self):
        passed = synthetic_result(self.scenarios[0])
        skipped = synthetic_result(self.scenarios[1], disposition=EvalDisposition.SKIPPED_PLATFORM)
        metrics = aggregate_metrics([passed, skipped])
        self.assertEqual(
            metrics["scenario_pass_rate"],
            {"numerator": 1, "denominator": 1, "percent": 100.0},
        )
        self.assertEqual(metrics["skipped_count"], 1)

    def test_synthetic_secret_is_absent_from_json_and_markdown_reports(self):
        scenario = next(item for item in self.scenarios if item.scenario_id == "security.fake_secret_redaction")
        result = EvaluationRunner(FIXTURES).run(scenario, SystemScenarioExecutor())
        self.assertTrue(result.passed, result.to_dict())
        secret = "gsk_SABLEEVALFAKESECRET4b8d1234567890"
        result.context_metrics[secret] = secret
        report = build_report([result], mode=EvalMode.DETERMINISTIC, repository_root=ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            json_path, markdown_path = write_report(report, tmp)
            rendered = json_path.read_text(encoding="utf-8") + markdown_path.read_text(encoding="utf-8")
        self.assertNotIn(secret, rendered)
        self.assertIn("[REDACTED]", rendered)

    def test_live_repeat_aggregation_does_not_need_a_real_provider(self):
        calls = []

        def fake_run_many(_runner, scenarios, _executor):
            batch = list(scenarios)
            calls.append([item.scenario_id for item in batch])
            return [
                EvalResult(
                    scenario_id=item.scenario_id,
                    category=item.category,
                    mode=EvalMode.LIVE,
                    expected_outcome=item.expected_outcome,
                    actual_outcome=item.expected_outcome,
                    disposition=EvalDisposition.PASS,
                    started_at="2026-01-01T00:00:00+00:00",
                    duration_ms=1,
                )
                for item in batch
            ]

        with tempfile.TemporaryDirectory() as tmp, patch(
            "sable.evals.cli.load_config",
            return_value={"main_model": "mock-model", "temperature": 0.0},
        ), patch(
            "sable.evals.cli.GroqClient",
            side_effect=AssertionError("provider must not be constructed by this mocked aggregation test"),
        ), patch.object(EvaluationRunner, "run_many", new=fake_run_many), redirect_stdout(io.StringIO()):
            exit_code = main(["--live", "--repeat", "2", "--output", tmp])
            report = json.loads(Path(tmp, "eval-results.json").read_text(encoding="utf-8"))
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(report["scenario_count"], 6)
        self.assertEqual(report["metrics"]["scenario_pass_rate"]["denominator"], 6)

    def test_ci_job_is_explicitly_deterministic_and_keyless(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        job = workflow.split("  deterministic-evals:", 1)[1]
        self.assertIn("needs: test", job)
        self.assertIn('GROQ_API_KEY: ""', job)
        self.assertIn("--baseline evals/baselines/m7-deterministic.json", job)
        self.assertNotIn("--live", job)


if __name__ == "__main__":
    unittest.main()
