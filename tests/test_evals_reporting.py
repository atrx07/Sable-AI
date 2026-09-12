import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sable.evals import (
    EvalDisposition,
    EvalMode,
    EvalResult,
    ExpectedOutcome,
    ScenarioCategory,
    aggregate_metrics,
    build_report,
    load_scenario_suite,
    write_report,
)
from sable.evals.cli import main


ROOT = Path(__file__).resolve().parents[1]


def result(identifier, *, passed=True, outcome=ExpectedOutcome.TASK_PASS, category=ScenarioCategory.CODING):
    return EvalResult(
        scenario_id=identifier,
        category=category,
        mode=EvalMode.DETERMINISTIC,
        expected_outcome=outcome,
        actual_outcome=outcome,
        disposition=EvalDisposition.PASS if passed else EvalDisposition.FAIL,
        started_at="2026-01-01T00:00:00+00:00",
        duration_ms=10,
        verified=passed,
        verification_status="PASS" if passed else "FAIL",
        model_turns=2,
        tool_calls=1,
        token_usage={"total_tokens": 7},
    )


class EvalReportingTests(unittest.TestCase):
    def test_metrics_include_counts_and_do_not_hide_security_failure(self):
        results = [
            result("coding.pass"),
            result(
                "security.fail",
                passed=False,
                outcome=ExpectedOutcome.CAPABILITY_DENIED,
                category=ScenarioCategory.SECURITY,
            ),
        ]
        metrics = aggregate_metrics(results)
        self.assertEqual(metrics["scenario_pass_rate"], {"numerator": 1, "denominator": 2, "percent": 50.0})
        self.assertEqual(metrics["security_scenario_pass_rate"]["numerator"], 0)
        self.assertEqual(metrics["security_scenario_pass_rate"]["denominator"], 1)

    def test_reports_are_machine_readable_and_human_readable(self):
        report = build_report([result("coding.pass")], mode="deterministic", repository_root=ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            json_path, markdown_path = write_report(report, tmp)
            loaded = json.loads(json_path.read_text(encoding="utf-8"))
            markdown = markdown_path.read_text(encoding="utf-8")
        self.assertEqual(loaded["schema_version"], 1)
        self.assertIn("1 / 1 (100.0%)", markdown)
        self.assertIn("coding.pass", markdown)

    def test_deterministic_cli_never_constructs_live_provider(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "sable.evals.cli.GroqClient", side_effect=AssertionError("live provider used")
        ):
            exit_code = main([
                "--scenario", "context.auth_selection",
                "--output", tmp,
            ])
            payload = json.loads(Path(tmp, "eval-results.json").read_text(encoding="utf-8"))
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["mode"], "DETERMINISTIC")
        self.assertIsNone(payload["provider"])

    def test_live_catalog_is_explicit_and_never_part_of_normal_suite(self):
        live = load_scenario_suite(ROOT / "evals" / "scenarios" / "m7.4-live.json")
        self.assertTrue(live)
        self.assertTrue(all(item.mode == EvalMode.LIVE for item in live))
        deterministic_ids = {
            item.scenario_id
            for name in ("m7.2-system.json", "m7.3-adversarial.json")
            for item in load_scenario_suite(ROOT / "evals" / "scenarios" / name)
        }
        self.assertFalse(deterministic_ids & {item.scenario_id for item in live})


if __name__ == "__main__":
    unittest.main()
