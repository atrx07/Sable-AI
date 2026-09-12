"""Stable, bounded machine-output contracts for Sable one-shot runs."""

from __future__ import annotations

from typing import Any

from .cli_args import exit_code_for_result
from .config import redact_secrets


SCHEMA_VERSION = 1


def _safe(value: Any, limit: int = 1000) -> str:
    return redact_secrets("" if value is None else str(value))[:limit]


def _items(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _verification_status(result: dict[str, Any], runtime: dict[str, Any]) -> str:
    loops = result.get("verification_loops", [])
    if isinstance(loops, list) and loops:
        latest = loops[-1]
        if isinstance(latest, dict):
            explicit = latest.get("overall_status") or latest.get("status")
            if explicit:
                return _safe(explicit, 60).upper()
    verification = runtime.get("verification", {})
    if isinstance(verification, dict):
        explicit = verification.get("overall_status") or verification.get("status")
        if explicit:
            return _safe(explicit, 60).upper()
    return "SKIPPED"


def _public_checks(result: dict[str, Any]) -> list[dict[str, Any]]:
    loops = result.get("verification_loops", [])
    if not isinstance(loops, list) or not loops or not isinstance(loops[-1], dict):
        return []
    checks: list[dict[str, Any]] = []
    for item in _items(loops[-1].get("checks", []))[:100]:
        if isinstance(item, dict):
            check = item.get("check", {}) if isinstance(item.get("check"), dict) else {}
            checks.append({
                "name": _safe(check.get("name", item.get("name", "check")), 160),
                "status": _safe(item.get("status", "UNKNOWN"), 60).upper(),
                "classification": _safe(item.get("classification", "NONE"), 80).upper(),
            })
            continue
        status = getattr(item, "status", "UNKNOWN")
        classification = getattr(item, "classification", "NONE")
        checks.append({
            "name": _safe(getattr(item, "name", "check"), 160),
            "status": _safe(getattr(status, "value", status), 60).upper(),
            "classification": _safe(getattr(classification, "value", classification), 80).upper(),
        })
    return checks


def build_json_result(
    result: dict[str, Any],
    *,
    verification_enabled: bool = True,
) -> dict[str, Any]:
    """Build schema v1 from structured task data without raw tool output."""
    runtime = result.get("runtime_task", {})
    if not isinstance(runtime, dict):
        runtime = {}
    final_status = _safe(result.get("final_status", "unknown"), 80).lower()
    if final_status in {"pass", "built", "plan"}:
        status = "completed"
    elif final_status == "cancelled":
        status = "cancelled"
    elif final_status in {"blocked", "verification_incomplete", "verification_blocked", "verification_integrity_blocked"}:
        status = "blocked"
    else:
        status = "failed"
    verification_status = _verification_status(result, runtime)
    purposes = _items(runtime.get("routing_purposes", []))
    fast_calls = sum(1 for purpose in purposes if str(purpose).upper() == "FAST_CONTEXT_SUMMARY")
    total_calls = _count(runtime.get("model_turn_count", result.get("agent_steps", 0)))
    public: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "result": final_status,
        "verified": bool(verification_enabled and verification_status in {"PASS", "PASS_WITH_OPTIONAL_SKIPS"}),
        "verification_enabled": bool(verification_enabled),
        "verification_status": verification_status,
        "task_id": _safe(result.get("task_id") or runtime.get("task_id"), 160) or None,
        "session_id": _safe(runtime.get("session_id"), 160) or None,
        "transaction_id": _safe(result.get("transaction_id") or runtime.get("transaction_id"), 160) or None,
        "changed_files": [_safe(path, 500) for path in _items(result.get("changed_files", []))[:500]],
        "commit": _safe(result.get("commit_sha"), 160) or None,
        "usage": {
            "main_model_calls": max(0, total_calls - fast_calls),
            "fast_model_calls": fast_calls,
            "input_tokens": _count(runtime.get("input_tokens", 0)),
            "output_tokens": _count(runtime.get("output_tokens", 0)),
            "total_tokens": _count(runtime.get("total_tokens", 0)),
        },
        "duration_ms": _count(runtime.get("duration_ms", 0)),
        "exit_reason": _safe(runtime.get("termination_reason"), 100) or None,
        "exit_code": int(exit_code_for_result(result)),
        "summary": _safe(result.get("chat_reply"), 4000),
        "verification_checks": _public_checks(result),
    }
    if result.get("undo_available"):
        public["undo_available"] = True
    if isinstance(result.get("rollback"), dict):
        public["rollback"] = {
            "success": bool(result["rollback"].get("success")),
            "changed_files": [
                _safe(path, 500) for path in _items(result["rollback"].get("changed_files", []))[:500]
            ],
        }
    warnings = [_safe(item, 500) for item in _items(result.get("trace_errors", []))[:20]]
    if warnings:
        public["warnings"] = warnings
    return public


__all__ = ["SCHEMA_VERSION", "build_json_result"]
