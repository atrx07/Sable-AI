import tempfile
import unittest
from pathlib import Path

from sable.evals import (
    EvalScenario,
    EvaluationRunner,
    SystemScenarioExecutor,
    load_scenario_suite,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evals" / "fixtures"
SUITE = ROOT / "evals" / "scenarios" / "m7.2-system.json"


class SystemEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenarios = load_scenario_suite(SUITE)

    def test_suite_has_unique_bounded_offline_cases(self):
        identifiers = [item.scenario_id for item in self.scenarios]
        self.assertEqual(len(identifiers), 24)
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertTrue(all("ci" in item.tags for item in self.scenarios))
        self.assertTrue(all(item.mode.value == "DETERMINISTIC" for item in self.scenarios))
        serialized = str([item.to_dict() for item in self.scenarios]).lower()
        self.assertNotIn("groq", serialized)
        self.assertNotIn("http://", serialized)
        self.assertNotIn("https://", serialized)

    def test_extended_scenario_fields_round_trip_and_reject_escape(self):
        source = self.scenarios[-2]
        restored = EvalScenario.from_dict(source.to_dict())
        self.assertEqual(restored, source)
        value = source.to_dict()
        value["post_run_writes"] = [{"path": "../escape.txt", "content": "no"}]
        with self.assertRaisesRegex(ValueError, "confined"):
            EvalScenario.from_dict(value)

    def test_real_system_scenarios(self):
        runner = EvaluationRunner(FIXTURES)
        executor = SystemScenarioExecutor()
        results = runner.run_many(self.scenarios, executor)
        failures = []
        for result in results:
            if not result.passed:
                failed_assertions = [
                    f"{item.kind}:{item.target}:{item.detail}"
                    for item in result.assertions
                    if not item.passed
                ]
                failures.append(
                    f"{result.scenario_id}: errors={result.errors!r}; "
                    f"assertions={failed_assertions!r}; notes={result.notes!r}"
                )
        self.assertEqual(failures, [], "\n".join(failures))

    def test_canonical_fixtures_remain_unchanged_after_suite(self):
        baseline = (FIXTURES / "bug_repair" / "calculator.py").read_text(encoding="utf-8")
        scenario = next(item for item in self.scenarios if item.scenario_id == "coding.simple_bug")
        EvaluationRunner(FIXTURES).run(scenario, SystemScenarioExecutor())
        self.assertEqual(
            (FIXTURES / "bug_repair" / "calculator.py").read_text(encoding="utf-8"),
            baseline,
        )


if __name__ == "__main__":
    unittest.main()
