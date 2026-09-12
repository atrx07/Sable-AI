"""Bounded JSON and Markdown benchmark reports."""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .metrics import aggregate_metrics
from .models import EvalMode, EvalResult, SCHEMA_VERSION


def _commit(root: Path) -> str | None:
    try:
        value = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=3, check=False,
        )
        return value.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def build_report(
    results: Iterable[EvalResult],
    *,
    mode: EvalMode | str,
    repository_root: str | Path,
    provider: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
) -> dict[str, Any]:
    items = list(results)
    selected_mode = mode.value if isinstance(mode, EvalMode) else str(mode).upper()
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": selected_mode,
        "sable_commit": _commit(Path(repository_root).resolve()),
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
        },
        "provider": provider if selected_mode == EvalMode.LIVE.value else None,
        "model": model if selected_mode == EvalMode.LIVE.value else None,
        "temperature": temperature if selected_mode == EvalMode.LIVE.value else None,
        "scenario_count": len(items),
        "metrics": aggregate_metrics(items),
        "failures": [item.scenario_id for item in items if item.disposition.value == "FAIL"],
        "skips": [
            {"scenario_id": item.scenario_id, "disposition": item.disposition.value, "notes": item.notes}
            for item in items if item.disposition.value.startswith("SKIPPED_")
        ],
        "results": [item.to_dict() for item in items],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Sable evaluation report",
        "",
        f"- Mode: `{report['mode']}`",
        f"- Commit: `{report.get('sable_commit') or 'unknown'}`",
        f"- Generated: `{report['generated_at']}`",
        f"- Scenarios: `{report['scenario_count']}`",
        "",
        "## Rate metrics",
        "",
        "| Metric | Result |",
        "|---|---:|",
    ]
    for name, value in report["metrics"].items():
        if isinstance(value, dict) and {"numerator", "denominator", "percent"} <= set(value):
            lines.append(f"| {name} | {value['numerator']} / {value['denominator']} ({value['percent']}%) |")
    lines.extend(["", "## Scenarios", "", "| Scenario | Disposition | Outcome | Verification |", "|---|---|---|---|"])
    for item in report["results"]:
        lines.append(
            f"| {item['scenario_id']} | {item['disposition']} | {item.get('actual_outcome') or '-'} | "
            f"{item.get('verification_status') or '-'} |"
        )
    if report["failures"]:
        lines.extend(["", "## Failures", "", *[f"- `{item}`" for item in report["failures"]]])
    if report["skips"]:
        lines.extend(["", "## Skips", "", *[
            f"- `{item['scenario_id']}` — {item['disposition']}" for item in report["skips"]
        ]])
    lines.extend([
        "",
        "> Percentages always include numerator and denominator. Skips are reported separately and are not passes.",
        "",
    ])
    return "\n".join(lines)


def write_report(report: dict[str, Any], output_dir: str | Path) -> tuple[Path, Path]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / "eval-results.json"
    markdown_path = target / "eval-report.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path


__all__ = ["build_report", "render_markdown", "write_report"]
