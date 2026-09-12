"""Public evaluation contracts and deterministic harness components."""

from .assertions import evaluate_assertion, evaluate_scenario
from .baseline import BASELINE_SCHEMA_VERSION, BaselineComparison, compare_baseline, load_baseline
from .fixtures import FixtureManager, MaterializedFixture, snapshot_tree
from .models import (
    AssertionKind,
    AssertionResult,
    EvalAssertion,
    EvalDisposition,
    EvalFileWrite,
    EvalMode,
    EvalResult,
    EvalScenario,
    ExpectedOutcome,
    RuntimeFixtureState,
    SCHEMA_VERSION,
    ScenarioCategory,
    ScenarioExecution,
    SecurityFixtureState,
    VerificationFixtureState,
    load_scenario_file,
    load_scenario_suite,
)
from .provider import ScriptedProvider, ScriptedRequest
from .metrics import aggregate_metrics
from .reporting import build_report, render_markdown, write_report
from .runner import EvaluationRunner, EvaluationSkip, ScenarioExecutor
from .system import SystemScenarioExecutor

__all__ = [
    "AssertionKind", "AssertionResult", "BASELINE_SCHEMA_VERSION", "BaselineComparison",
    "EvalAssertion", "EvalDisposition", "EvalFileWrite", "EvalMode",
    "EvalResult", "EvalScenario", "EvaluationRunner", "EvaluationSkip", "ExpectedOutcome",
    "FixtureManager", "MaterializedFixture", "RuntimeFixtureState", "SCHEMA_VERSION", "ScenarioCategory",
    "ScenarioExecution", "ScenarioExecutor", "ScriptedProvider", "ScriptedRequest", "SecurityFixtureState",
    "SystemScenarioExecutor", "VerificationFixtureState", "aggregate_metrics", "build_report",
    "compare_baseline", "evaluate_assertion", "evaluate_scenario", "load_baseline", "load_scenario_file", "load_scenario_suite",
    "render_markdown", "snapshot_tree", "write_report",
]
