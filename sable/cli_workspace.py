"""CLI project, filesystem and Git slash commands."""

from __future__ import annotations

import shutil
from pathlib import Path

from .ui import ACCENT, B, BLU, CYN, DIM, GRN, RED, R, YLW


class WorkspaceCommandsMixin:
    def _cmd_sandbox(self, arg: str = "") -> None:
        if arg.strip():
            print(f"  {RED}Usage: /sandbox{R}")
            return
        if self.executor is None:
            print(f"  {RED}Execution backend is unavailable.{R}")
            return
        status = self.executor.execution_backend_status()
        guarantees = status.get("guarantees", {})
        print(f"\n{B}Execution backend{R}")
        print(f"  Backend      {status.get('name', '?')}")
        print(f"  Available    {'yes' if status.get('available') else 'no'}")
        if status.get("reason"):
            print(f"  Detail       {status['reason']}")
        labels = (
            ("private_home", "Private HOME"),
            ("sanitized_environment", "Sanitized env"),
            ("workspace_path_confinement", "Workspace paths"),
            ("filesystem_namespace", "Filesystem namespace"),
            ("network_isolation", "Network isolation"),
            ("process_isolation", "Process isolation"),
            ("resource_limits", "Resource limits"),
            ("descendant_cleanup", "Descendant cleanup"),
            ("shell_disabled_by_default", "Shell default"),
        )
        for key, label in labels:
            print(f"  {label:<20} {guarantees.get(key, 'NOT_SUPPORTED')}")

    def _cmd_undo(self, arg: str) -> None:
        assert self.executor is not None
        parts = arg.split()
        dry_run = "--dry-run" in parts
        identifiers = [part for part in parts if part != "--dry-run"]
        if len(identifiers) > 1:
            print(f"  {RED}Usage: /undo [transaction-id] [--dry-run]{R}")
            return
        result = self.executor.undo_transaction(
            identifiers[0] if identifiers else None,
            dry_run=dry_run,
        )
        color = GRN if result.success else YLW
        print(f"  {color}{result.output or result.error}{R}")

    def _cmd_transaction(self, arg: str) -> None:
        assert self.executor is not None
        parts = arg.split()
        if not parts:
            result = self.executor.transaction_status()
        elif parts[0].lower() == "list" and len(parts) == 1:
            result = self.executor.transaction_list()
        elif parts[0].lower() == "show" and len(parts) == 2:
            result = self.executor.transaction_status(parts[1])
        else:
            print(f"  {RED}Usage: /txn [list|show <transaction-id>]{R}")
            return
        color = "" if result.success else RED
        print(f"\n{color}{result.output or result.error}{R if color else ''}")

    def _cmd_session(self, arg: str) -> None:
        manager = getattr(self, "sessions", None)
        if manager is None:
            detail = getattr(self, "session_error", None) or "session persistence is unavailable"
            print(f"  {YLW}Sessions unavailable: {detail}{R}")
            return
        parts = arg.strip().split()
        if not parts:
            print(f"\n{manager.summary_text()}")
            return
        if parts[0].lower() == "list" and len(parts) == 1:
            print(f"\n{B}Sessions:{R}")
            for item in manager.list_sessions():
                marker = f"{ACCENT}●{R}" if manager.current and item.get("session_id") == manager.current.session_id else f"{DIM}•{R}"
                print(
                    f"  {marker} {item.get('session_id', '?')}  {item.get('status', '?')}"
                    f"  tasks={len(item.get('task_ids', []))}"
                    f"  tokens={item.get('total_tokens', 0)}"
                    f"  updated={item.get('updated_at', '?')}"
                )
            return
        if parts[0].lower() == "show" and len(parts) == 2:
            print(f"\n{manager.summary_text(parts[1])}")
            return
        print(f"  {RED}Usage: /session [list|show <session-id>]{R}")

    def _cmd_trace(self, arg: str) -> None:
        manager = getattr(self, "sessions", None)
        if manager is None:
            detail = getattr(self, "session_error", None) or "session persistence is unavailable"
            print(f"  {YLW}Trace unavailable: {detail}{R}")
            return
        task_id = arg.strip()
        if len(task_id.split()) > 1:
            print(f"  {RED}Usage: /trace [task-id]{R}")
            return
        print(f"\n{manager.trace_text(task_id=task_id or None)}")

    def _cmd_projects(self) -> None:
        base = Path(self.cfg["project_dir"]).expanduser()
        base.mkdir(parents=True, exist_ok=True)
        print(f"\n{B}Projects:{R}")
        for p in sorted(base.iterdir()):
            if p.is_dir():
                marker = f"{ACCENT}●{R}" if p.name == self.current_project else f"{DIM}•{R}"
                print(f"  {marker} {p.name}")
    def _cmd_project(self, arg: str) -> None:
        name = arg.strip()
        if not name:
            self._cmd_projects()
            return
        if name.startswith("delete "):
            target_name = name[7:].strip()
            if not target_name or "/" in target_name or "\\" in target_name or target_name in {".", ".."}:
                print(f"  {RED}Invalid project name.{R}")
                return
            target = Path(self.cfg["project_dir"]).expanduser() / target_name
            if target_name == self.current_project:
                print(f"  {RED}Switch away before deleting the active project.{R}")
                return
            if input(f"  {YLW}Delete project '{target_name}' permanently? [y/N]: {R}").strip().lower() == "y":
                shutil.rmtree(target, ignore_errors=True)
                print(f"  {GRN}Deleted {target_name}.{R}")
            return
        if "/" in name or "\\" in name or name in {".", ".."}:
            print(f"  {RED}Project names cannot contain path separators.{R}")
            return
        self._setup_project(name)
        print(f"  {GRN}Project: {name}{R}")
    def _cmd_git(self, arg: str) -> None:
        assert self.executor is not None
        parts = arg.strip().split(None, 1)
        sub = parts[0].lower() if parts else "status"
        rest = parts[1] if len(parts) > 1 else ""
        if sub == "init":
            r = self.executor.git_init(rest.strip() or None)
        elif sub == "remote":
            if not rest.strip():
                print(f"  {RED}Usage: /git remote <url>{R}")
                return
            r = self.executor.git_set_remote(rest.strip())
        elif sub == "status":
            r = self.executor.git_status()
        elif sub == "diff":
            r = self.executor.git_diff(rest.strip())
        elif sub == "log":
            try:
                n = int(rest.strip() or "10")
            except ValueError:
                n = 10
            r = self.executor.git_log(n)
        elif sub == "branch":
            r = self.executor.git_branch(rest.strip())
        elif sub == "add":
            r = self.executor.git_add(rest.strip() or ".")
        elif sub == "commit":
            message = rest.strip() or input("  Commit message: ").strip()
            r = self.executor.git_commit(message)
        elif sub == "push":
            r = self.executor.git_push(rest.strip())
        elif sub == "pull":
            r = self.executor.git_pull(rest.strip())
        elif sub == "clone":
            clone_parts = rest.split(None, 1)
            if not clone_parts:
                print(f"  {RED}Usage: /git clone <url> [dest]{R}")
                return
            r = self.executor.git_clone(clone_parts[0], clone_parts[1] if len(clone_parts) > 1 else "")
        elif sub == "stash":
            r = self.executor.git_stash(rest.strip() or "push")
        elif sub == "creds":
            print(f"  {YLW}Sable v2 does not store GitHub PATs. Configure SSH or your normal Git credential helper instead.{R}")
            return
        else:
            print(f"  {RED}Unknown /git subcommand.{R}")
            return
        color = GRN if r.success else RED
        print(f"  {color}{r.output or r.error or 'Done.'}{R}")
    def _handle_file_command(self, cmd: str, arg: str) -> bool:
        assert self.executor is not None
        if cmd == "ls":
            r = self.executor.list_files(arg or ".")
        elif cmd == "cat":
            r = self.executor.read_file(arg) if arg else None
        elif cmd == "mkdir":
            r = self.executor.make_dir(arg) if arg else None
        elif cmd == "rm":
            if not arg:
                r = None
            elif input(f"  {YLW}Delete '{arg}'? [y/N]: {R}").strip().lower() == "y":
                r = self.executor.delete_file(arg)
            else:
                print(f"  {DIM}Cancelled.{R}")
                return True
        elif cmd in {"cp", "mv"}:
            parts = arg.split(None, 1)
            if len(parts) != 2:
                r = None
            else:
                r = self.executor.copy_file(*parts) if cmd == "cp" else self.executor.move_file(*parts)
        elif cmd == "find":
            r = self.executor.search_files(arg) if arg else None
        elif cmd == "grep":
            parts = arg.split(None, 1)
            r = self.executor.grep_files(parts[0], ext=parts[1] if len(parts) > 1 else "") if parts else None
        elif cmd == "info":
            r = self.executor.file_info(arg) if arg else None
        elif cmd == "df":
            r = self.executor.disk_usage()
        elif cmd == "cd":
            r = self.executor.change_dir(arg) if arg else None
        elif cmd == "pwd":
            print(f"  {BLU}{self.executor.current_dir}{R}")
            return True
        else:
            return False
        if r is None:
            print(f"  {RED}Missing or invalid arguments. Type /help.{R}")
        else:
            print(f"\n{r.output if r.success else RED + r.error + R}")
        return True
