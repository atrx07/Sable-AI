"""Command-line entry point for deterministic and explicitly enabled live evals."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from ..config import load_config
from ..groq_client import GroqClient
from .baseline import compare_baseline, load_baseline
from .metrics import aggregate_metrics
from .models import EvalDisposition, EvalMode, load_scenario_suite
from .reporting import build_report, write_report
from .runner import EvaluationRunner
from .system import SystemScenarioExecutor


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Sable's isolated evaluation suites.")
    parser.add_argument("--live", action="store_true", help="Explicitly allow configured-provider live evals.")
    parser.add_argument("--scenario", action="append", default=[], help="Exact scenario id; repeat to select several.")
    parser.add_argument("--category", action="append", default=[], help="Scenario category; repeat to select several.")
    parser.add_argument("--repeat", type=int, default=1, help="Run each selected scenario N times (1-20).")
    parser.add_argument("--output", help="Report directory (default: evals/reports/generated/<timestamp>).")
    parser.add_argument("--baseline", help="Compare a complete deterministic run with a baseline JSON file.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not 1 <= args.repeat <= 20:
        raise SystemExit("--repeat must be between 1 and 20")
    if args.baseline and (args.live or args.scenario or args.category or args.repeat != 1):
        raise SystemExit("--baseline requires an unfiltered deterministic run with --repeat 1")
    root = _root()
    scenario_files = (
        [root / "evals" / "scenarios" / "m7.4-live.json"]
        if args.live else [
            root / "evals" / "scenarios" / "m7.2-system.json",
            root / "evals" / "scenarios" / "m7.3-adversarial.json",
            root / "evals" / "scenarios" / "m7.5-resilience.json",
        ]
    )
    scenarios = [item for path in scenario_files for item in load_scenario_suite(path)]
    selected_ids = set(args.scenario)
    selected_categories = {item.upper() for item in args.category}
    if selected_ids:
        scenarios = [item for item in scenarios if item.scenario_id in selected_ids]
    if selected_categories:
        scenarios = [item for item in scenarios if item.category.value in selected_categories]
    if not scenarios:
        raise SystemExit("No evaluation scenarios matched the supplied filters.")

    provider_name = model = None
    temperature = None
    if args.live:
        cfg = load_config()
        model = str(cfg["main_model"])
        temperature = float(cfg.get("temperature", 0.2))
        provider_name = "groq"
        provider_factory = lambda _script: GroqClient(cfg, model, temperature)
    else:
        provider_factory = None

    runner = EvaluationRunner(
        root / "evals" / "fixtures",
        allow_live=args.live,
        **({"provider_factory": provider_factory} if provider_factory else {}),
    )
    results = []
    for repetition in range(1, args.repeat + 1):
        batch = runner.run_many(scenarios, SystemScenarioExecutor())
        for result in batch:
            result.notes.append(f"repetition_index={repetition}")
        results.extend(batch)

    mode = EvalMode.LIVE if args.live else EvalMode.DETERMINISTIC
    report = build_report(
        results,
        mode=mode,
        repository_root=root,
        provider=provider_name,
        model=model,
        temperature=temperature,
    )
    baseline_passed = True
    if args.baseline:
        comparison = compare_baseline(report, load_baseline(Path(args.baseline).resolve()))
        report["baseline"] = comparison.to_dict()
        baseline_passed = comparison.passed
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = Path(args.output).resolve() if args.output else root / "evals" / "reports" / "generated" / timestamp
    json_path, markdown_path = write_report(report, output)
    metrics = aggregate_metrics(results)["scenario_pass_rate"]
    print(f"Evaluation report: {markdown_path}")
    print(f"Machine results: {json_path}")
    print(f"Passed: {metrics['numerator']}/{metrics['denominator']} ({metrics['percent']}%)")
    if args.baseline:
        print(f"Baseline: {'PASS' if baseline_passed else 'FAIL'}")
    return 0 if baseline_passed and all(
        item.disposition != EvalDisposition.FAIL for item in results
    ) else 1


if __name__ == "__main__":
    sys.exit(main())
