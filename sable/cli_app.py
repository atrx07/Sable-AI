"""Top-level Sable command application.

This module owns process arguments and exit codes. The interactive ``CLI``
class remains responsible for the established slash-command shell.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Sequence, TextIO

from .cli import CLI
from .cli_args import ExitCode, exit_code_for_result, parse_cli_args, resolve_workspace, version_text
from .doctor import diagnose, render_text as render_doctor_text


def _apply_overrides(cli: CLI, *, mode: str | None, verify: str | None) -> None:
    if mode:
        cli.mode = mode
    if verify == "off":
        cli.verify_enabled = False
    elif verify:
        cli.verify_enabled = True
        cli.verification_scope = verify


def _basic_doctor(workspace: Path, *, stdout: TextIO) -> ExitCode:
    report = diagnose(workspace)
    print(render_doctor_text(report), file=stdout)
    return report.exit_code


def run_cli(
    argv: Sequence[str] | None = None,
    *,
    cli_factory: Callable[..., CLI] = CLI,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    stdin_isatty: bool | None = None,
) -> int:
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    options = parse_cli_args(list(argv or []))
    if options.json_output and options.command != "run":
        print("sable: --json is supported only with `sable run`.", file=stderr)
        return int(ExitCode.USAGE)
    if options.command == "version":
        print(version_text(), file=stdout)
        return int(ExitCode.SUCCESS)

    try:
        workspace = resolve_workspace(options.workspace) if options.workspace is not None else None
    except ValueError as exc:
        print(f"sable: {exc}", file=stderr)
        return int(ExitCode.USAGE)

    if options.command == "doctor":
        assert workspace is not None
        return int(_basic_doctor(workspace, stdout=stdout))

    interactive_input = sys.stdin.isatty() if stdin_isatty is None else bool(stdin_isatty)
    cli = cli_factory(
        workspace=workspace,
        interactive_approvals=interactive_input,
    )
    configure = getattr(cli, "configure_presentation", None)
    if configure is not None:
        configure(
            plain=bool(options.plain),
            no_color=bool(options.no_color),
            quiet=bool(options.quiet),
            verbose=bool(options.verbose),
            json_output=bool(options.json_output),
            stdout=stdout,
            stderr=stderr,
        )
    else:
        cli.plain = bool(options.plain)
        cli.no_color = bool(options.no_color)
        cli.quiet = bool(options.quiet)
        cli.verbose = bool(options.verbose)
    _apply_overrides(cli, mode=options.mode, verify=options.verify)

    if options.command in {"legacy", "chat"}:
        cli.run()
        return int(ExitCode.SUCCESS)

    if options.command == "run":
        result = cli.run_once(options.task or "")
        cli._print_result(result)
        return int(exit_code_for_result(result))

    print(f"sable: unsupported command {options.command}", file=stderr)
    return int(ExitCode.USAGE)


def main(argv: Sequence[str] | None = None) -> int:
    return run_cli(sys.argv[1:] if argv is None else argv)


__all__ = ["main", "run_cli"]
