import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from sable.evals import (
    AssertionKind,
    EvalAssertion,
    EvalDisposition,
    EvalMode,
    EvalResult,
    EvalScenario,
    EvaluationRunner,
    ExpectedOutcome,
    FixtureManager,
    ScenarioCategory,
    ScenarioExecution,
    ScriptedProvider,
    evaluate_assertion,
    load_scenario_file,
    snapshot_tree,
)
from sable.main_agent import MainAgent
from sable.providers import ProviderError
from sable.tools import ToolExecutor


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evals" / "fixtures"
SMOKE_SCENARIO = ROOT / "evals" / "scenarios" / "framework-smoke.json"


def scenario(**updates):
    value = {
        "scenario_id": "framework.unit",
        "title": "Framework unit scenario",
        "category": "FAILURE_HANDLING",
        "description": "Exercises the typed evaluation contracts.",
        "fixture": "framework_smoke",
        "task_prompt": "Inspect the fixture.",
        "expected_outcome": "TASK_PASS",
    }
    value.update(updates)
    return EvalScenario.from_dict(value)


class EvalModelTests(unittest.TestCase):
    def test_scenario_load_and_round_trip_preserve_typed_contract(self):
        loaded = load_scenario_file(SMOKE_SCENARIO)
        self.assertEqual(loaded.scenario_id, "framework.smoke")
        self.assertEqual(loaded.mode, EvalMode.DETERMINISTIC)
        self.assertEqual(loaded.expected_outcome, ExpectedOutcome.TASK_PASS)
        self.assertEqual(EvalScenario.from_dict(loaded.to_dict()), loaded)

    def test_malformed_scenarios_fail_before_execution(self):
        invalid = (
            {},
            {**scenario().to_dict(), "scenario_id": "BAD ID"},
            {**scenario().to_dict(), "fixture": "../escape"},
            {**scenario().to_dict(), "category": "MARKETING"},
            {**scenario().to_dict(), "max_tool_calls": 0},
            {**scenario().to_dict(), "assertions": "not-a-list"},
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                EvalScenario.from_dict(value)

    def test_result_schema_is_versioned_bounded_and_secret_redacted(self):
        secret = "gsk_SABLEEVALFAKETOKENDONOTUSE123456"
        result = EvalResult(
            scenario_id="framework.unit",
            category=ScenarioCategory.SECURITY,
            mode=EvalMode.DETERMINISTIC,
            expected_outcome=ExpectedOutcome.SAFE_REFUSAL,
            actual_outcome=ExpectedOutcome.SAFE_REFUSAL,
            disposition=EvalDisposition.PASS,
            started_at="2026-01-01T00:00:00+00:00",
            duration_ms=1,
            errors=[secret],
        ).to_dict()
        serialized = json.dumps(result)
        self.assertEqual(result["schema_version"], 1)
        self.assertNotIn(secret, serialized)
        self.assertIn("[REDACTED]", serialized)


class FixtureTests(unittest.TestCase):
    def test_materialization_is_exact_temporary_and_resets_each_run(self):
        manager = FixtureManager(FIXTURES)
        source = FIXTURES / "framework_smoke"
        original = snapshot_tree(source)
        with manager.materialize("framework_smoke") as first:
            Path(first.path, "app.py").write_text("changed\n", encoding="utf-8")
            Path(first.path, "new.txt").write_text("temporary\n", encoding="utf-8")
            first_path = first.path
        self.assertFalse(first_path.exists())
        self.assertEqual(snapshot_tree(source), original)
        with manager.materialize("framework_smoke") as second:
            self.assertEqual(snapshot_tree(second.path), original)
            self.assertFalse(Path(second.path, "new.txt").exists())

    def test_fixture_paths_cannot_escape_canonical_root(self):
        manager = FixtureManager(FIXTURES)
        for target in ("../fixtures", str(FIXTURES.resolve())):
            with self.subTest(target=target), self.assertRaises(ValueError):
                with manager.materialize(target):
                    pass


class ScriptedProviderTests(unittest.TestCase):
    def test_provider_returns_exact_sequence_and_records_bounded_requests(self):
        provider = ScriptedProvider([
            {"tool_calls": [{"name": "read_file", "arguments": {"path": "app.py"}}]},
            {"content": "Done."},
        ])
        first = provider.complete([{"role": "user", "content": "inspect"}], tools=[{"type": "function"}])
        second = provider.complete([{"role": "tool", "content": "ok"}])
        self.assertEqual(first.tool_calls[0].name, "read_file")
        self.assertEqual(second.content, "Done.")
        self.assertEqual(provider.remaining, 0)
        self.assertTrue(provider.requests[0].tools_enabled)
        with self.assertRaises(ProviderError):
            provider.complete([])

    def test_malformed_script_is_rejected_during_construction(self):
        for script in ([{"tool_calls": "bad"}], [{"tool_calls": [{"name": "x", "arguments": "bad"}]}]):
            with self.subTest(script=script), self.assertRaises(ValueError):
                ScriptedProvider(script)


class AssertionTests(unittest.TestCase):
    def test_file_result_event_and_exit_assertions_are_machine_checked(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            Path(workspace, "same.txt").write_text("alpha\n", encoding="utf-8")
            baseline = snapshot_tree(workspace)
            execution = ScenarioExecution(
                ExpectedOutcome.TASK_PASS,
                runtime_result={"nested": {"status": "PASS"}},
                exit_code=0,
                events=[{"event_type": "TASK_COMPLETED"}],
            )
            assertions = (
                EvalAssertion(AssertionKind.FILE_EXISTS, "same.txt"),
                EvalAssertion(AssertionKind.FILE_ABSENT, "missing.txt"),
                EvalAssertion(AssertionKind.FILE_CONTAINS, "same.txt", "alpha"),
                EvalAssertion(AssertionKind.FILE_UNCHANGED, "same.txt"),
                EvalAssertion(AssertionKind.RESULT_EQUALS, "nested.status", "PASS"),
                EvalAssertion(AssertionKind.EVENT_OCCURRED, expected="TASK_COMPLETED"),
                EvalAssertion(AssertionKind.EXIT_CODE_EQUALS, expected=0),
            )
            results = [
                evaluate_assertion(item, workspace=workspace, baseline=baseline, execution=execution)
                for item in assertions
            ]
        self.assertTrue(all(item.passed for item in results))


class EvaluationRunnerTests(unittest.TestCase):
    @staticmethod
    def real_agent_executor(eval_scenario, workspace, provider):
        events = []
        executor = ToolExecutor(workspace, transaction_storage_dir=workspace.parent / "transactions")
        agent = MainAgent(
            provider,
            executor,
            max_steps=eval_scenario.max_model_turns,
            max_tool_calls=eval_scenario.max_tool_calls,
            on_event=lambda event_type, metadata: events.append({"event_type": event_type.value, "metadata": metadata}),
        )
        result = agent.run(eval_scenario.task_prompt, mode="plan")
        return ScenarioExecution(
            ExpectedOutcome.TASK_PASS,
            runtime_result=result,
            changed_files=result["changed_files"],
            events=events,
            model_turns=result["model_calls"],
            tool_calls=result["tool_calls"],
        )

    def test_runner_drives_real_agent_tool_path_in_temporary_fixture(self):
        loaded = load_scenario_file(SMOKE_SCENARIO)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            result = EvaluationRunner(FIXTURES).run(loaded, self.real_agent_executor)
        self.assertTrue(result.passed, result.to_dict())
        self.assertEqual(result.actual_outcome, ExpectedOutcome.TASK_PASS)
        self.assertEqual(result.model_turns, 2)
        self.assertEqual(result.tool_calls, 1)
        self.assertTrue(all(item.passed for item in result.assertions))

    def test_live_scenario_is_explicitly_skipped_not_passed(self):
        live = scenario(mode="LIVE")
        called = False

        def executor(*_args):
            nonlocal called
            called = True
            return ScenarioExecution(ExpectedOutcome.TASK_PASS)

        result = EvaluationRunner(FIXTURES).run(live, executor)
        self.assertFalse(called)
        self.assertFalse(result.passed)
        self.assertEqual(result.disposition, EvalDisposition.SKIPPED_LIVE_DISABLED)

    def test_runner_reports_assertion_failure_and_duplicate_ids(self):
        item = scenario(assertions=[{"kind": "FILE_EXISTS", "target": "missing.txt"}])
        runner = EvaluationRunner(FIXTURES)
        result = runner.run(item, lambda *_args: ScenarioExecution(ExpectedOutcome.TASK_PASS))
        self.assertEqual(result.disposition, EvalDisposition.FAIL)
        self.assertTrue(any(not assertion.passed for assertion in result.assertions))
        with self.assertRaisesRegex(ValueError, "duplicate scenario"):
            runner.run_many([item, item], lambda *_args: ScenarioExecution(ExpectedOutcome.TASK_PASS))


if __name__ == "__main__":
    unittest.main()
