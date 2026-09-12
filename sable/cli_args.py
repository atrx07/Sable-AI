"""Stable top-level command parsing and process exit contracts for Sable."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Sequence

from . import __version__
from .config import is_blocked_path


class ExitCode(IntEnum):
    SUCCESS = 0
    USAGE = 2
    VERIFICATION = 10
    CAPABILITY_DENIED = 20
    BACKEND_UNAVAILABLE = 21
    PROVIDER_FAILURE = 30
    INTERNAL_ERROR = 70
    CANCELLED = 130


@dataclass(frozen=True)
class CLIOptions:
    command: str
    workspace: str | None = None
    task: str | None = None
    mode: str | None = None
    verify: str | None = None
    no_color: bool = False
    plain: bool = False
    quiet: bool = False
    verbose: bool = False
    json_output: bool = False
    version: bool = False


def _add_global_options(parser: argparse.ArgumentParser, *, suppress_defaults: bool = False) -> None:
    default = argparse.SUPPRESS if suppress_defaults else None
    parser.add_argument("--no-color", action="store_true", default=default, help="disable ANSI color")
    parser.add_argument("--plain", action="store_true", default=default, help="use stable plain-text output")
    parser.add_argument("--quiet", action="store_true", default=default, help="show only the final outcome")
    parser.add_argument("--verbose", action="store_true", default=default, help="show additional bounded detail")
    parser.add_argument("--json", action="store_true", default=default, help="emit one versioned JSON result")
    parser.add_argument("--mode", choices=("plan", "build", "yolo"), default=default, help="override task mode")
    parser.add_argument(
        "--verify", choices=("quick", "affected", "full", "off"), default=default,
        help="override verification for this invocation",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sable",
        description="Bounded, workspace-scoped agentic coding assistant.",
    )
    _add_global_options(parser)
    parser.add_argument("--version", action="store_true", help="show the Sable version and exit")
    subcommands = parser.add_subparsers(dest="command")

    chat = subcommands.add_parser("chat", help="open an interactive Sable session")
    _add_global_options(chat, suppress_defaults=True)
    chat.add_argument("workspace", nargs="?", default=".", help="existing workspace directory")

    run = subcommands.add_parser("run", help="run exactly one task and exit")
    _add_global_options(run, suppress_defaults=True)
    run.add_argument("task", help="natural-language task")
    run.add_argument("workspace", nargs="?", default=".", help="existing workspace directory")

    doctor = subcommands.add_parser("doctor", help="inspect local Sable readiness")
    _add_global_options(doctor, suppress_defaults=True)
    doctor.add_argument("workspace", nargs="?", default=".", help="existing workspace directory")
    return parser


def _normalize_bare_workspace(argv: list[str]) -> list[str]:
    """Translate ``sable PATH`` into ``sable chat PATH`` without hiding bad options."""
    commands = {"chat", "run", "doctor"}
    options_with_values = {"--mode", "--verify"}
    skip_next = False
    for index, token in enumerate(argv):
        if skip_next:
            skip_next = False
            continue
        if token in options_with_values:
            skip_next = True
            continue
        if token.startswith("-"):
            continue
        if token in commands:
            return argv
        return argv[:index] + ["chat"] + argv[index:]
    return argv


def parse_cli_args(argv: Sequence[str] | None = None) -> CLIOptions:
    values = _normalize_bare_workspace(list(argv or []))
    namespace = build_parser().parse_args(values)
    command = namespace.command or ("version" if namespace.version else "legacy")
    return CLIOptions(
        command=command,
        workspace=getattr(namespace, "workspace", None),
        task=getattr(namespace, "task", None),
        mode=getattr(namespace, "mode", None),
        verify=getattr(namespace, "verify", None),
        no_color=bool(getattr(namespace, "no_color", False)),
        plain=bool(getattr(namespace, "plain", False)),
        quiet=bool(getattr(namespace, "quiet", False)),
        verbose=bool(getattr(namespace, "verbose", False)),
        json_output=bool(getattr(namespace, "json", False)),
        version=bool(getattr(namespace, "version", False)),
    )


def resolve_workspace(value: str, *, cwd: str | Path | None = None) -> Path:
    raw = Path(value).expanduser()
    if not raw.is_absolute():
        raw = Path(cwd or Path.cwd()) / raw
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"Workspace does not exist: {value}") from exc
    if not resolved.is_dir():
        raise ValueError(f"Workspace is not a directory: {value}")
    if is_blocked_path(str(resolved)):
        raise ValueError(f"Workspace is a protected path: {value}")
    return resolved


def exit_code_for_result(result: dict) -> ExitCode:
    final_status = str(result.get("final_status", "")).lower()
    runtime = result.get("runtime_task", {})
    if not isinstance(runtime, dict):
        runtime = {}
    reason = str(runtime.get("termination_reason", "")).upper()
    if final_status in {"pass", "built", "plan"}:
        return ExitCode.SUCCESS
    if final_status == "configuration_error":
        return ExitCode.USAGE
    if final_status.startswith("verification_") or final_status == "repair_no_progress":
        return ExitCode.VERIFICATION
    if reason == "BACKEND_UNAVAILABLE":
        return ExitCode.BACKEND_UNAVAILABLE
    if reason in {
        "CAPABILITY_DENIED", "SANDBOX_POLICY_BLOCKED", "POLICY_BLOCKED",
        "TOOL_BUDGET_EXHAUSTED", "MODEL_TURN_LIMIT",
    } or final_status == "blocked":
        return ExitCode.CAPABILITY_DENIED
    if reason == "PROVIDER_FAILURE" or final_status == "provider_error":
        return ExitCode.PROVIDER_FAILURE
    if reason == "USER_ABORT" or final_status == "cancelled":
        return ExitCode.CANCELLED
    return ExitCode.INTERNAL_ERROR


def version_text() -> str:
    return f"Sable {__version__}"


__all__ = [
    "CLIOptions", "ExitCode", "build_parser", "exit_code_for_result",
    "parse_cli_args", "resolve_workspace", "version_text",
]
