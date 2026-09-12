import unittest
from pathlib import Path

from sable.evals import EvaluationRunner, SystemScenarioExecutor, load_scenario_suite


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evals" / "fixtures"
SUITE = ROOT / "evals" / "scenarios" / "m7.3-adversarial.json"


class AdversarialEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenarios = load_scenario_suite(SUITE)

    def test_suite_is_offline_and_uses_only_synthetic_secrets(self):
        self.assertEqual(len(self.scenarios), 21)
        serialized = str([item.to_dict() for item in self.scenarios])
        self.assertNotIn("attacker.example/", serialized)
        self.assertNotIn("github.com", serialized.lower())
        self.assertNotIn("groq", serialized.lower())
        self.assertTrue(all("ci" in item.tags for item in self.scenarios))

    def test_adversarial_scenarios(self):
        results = EvaluationRunner(FIXTURES).run_many(self.scenarios, SystemScenarioExecutor())
        failures = []
        for result in results:
            failed = [
                f"{item.kind}:{item.target}:{item.detail}"
                for item in result.assertions
                if not item.passed
            ]
            if result.errors or failed:
                failures.append(f"{result.scenario_id}: errors={result.errors!r}; assertions={failed!r}")
        self.assertEqual(failures, [], "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
