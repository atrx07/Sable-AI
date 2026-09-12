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
from .config import get_active_key, load_config
from .tools import ToolExecutor


def _apply_overrides(cli: CLI, *, mode: str | None, verify: str | None) -> None:
    if mode:
        cli.mode = mode
    if verify == "off":
        cli.verify_enabled = False
    elif verify:
        cli.verify_enabled = True
        cli.verification_scope = verify


def _basic_doctor(workspace: Path, *, stdout: TextIO) -> ExitCode:
    cfg = load_config()
    key, _ = get_active_key(cfg)
    executor = ToolExecutor(
        workspace,
        command_timeout=cfg.get("command_timeout", 120),
        execution_backend=cfg.get("execution_backend", "auto"),
        proot_rootfs=cfg.get("proot_rootfs") or None,
    )
    backend = executor.execution_backend_status()
    print("Sable doctor", file=stdout)
    print(f"Workspace  OK  {workspace}", file=stdout)
    print(f"Git        {'OK' if (workspace / '.git').exists() else 'INFO'}  "
          f"{'repository detected' if (workspace / '.git').exists() else 'non-Git workspace'}", file=stdout)
    print(f"Provider   {'OK' if key else 'WARN'}  "
          f"{'Groq key configured' if key else 'Groq key not configured'}", file=stdout)
    print(f"Backend    {'OK' if backend.get('available') else 'ERROR'}  {backend.get('name', 'unknown')}", file=stdout)
    if backend.get("reason"):
        print(f"Detail     {backend['reason']}", file=stdout)
    return ExitCode.SUCCESS if backend.get("available") else ExitCode.BACKEND_UNAVAILABLE


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
        if options.quiet:
            print(str(result.get("final_status", "unknown")), file=stdout)
        else:
            cli._print_result(result)
        return int(exit_code_for_result(result))

    print(f"sable: unsupported command {options.command}", file=stderr)
    return int(ExitCode.USAGE)


def main(argv: Sequence[str] | None = None) -> int:
    return run_cli(sys.argv[1:] if argv is None else argv)


__all__ = ["main", "run_cli"]
