"""CLI project, filesystem and Git slash commands."""

from __future__ import annotations

import shutil
from pathlib import Path

from .presentation import PlainRenderer


class WorkspaceCommandsMixin:
    def _command_renderer(self):
        return getattr(self, "renderer", None) or PlainRenderer()

    def _cmd_sandbox(self, arg: str = "") -> None:
        if arg.strip():
            self._command_renderer().message("Usage: /sandbox")
            return
        if self.executor is None:
            self._command_renderer().message("Execution backend is unavailable.")
            return
        status = self.executor.execution_backend_status()
        guarantees = status.get("guarantees", {})
        lines = [
            "Execution backend",
            f"  Backend      {status.get('name', '?')}",
            f"  Available    {'yes' if status.get('available') else 'no'}",
        ]
        if status.get("reason"):
            lines.append(f"  Detail       {status['reason']}")
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
            lines.append(f"  {label:<20} {guarantees.get(key, 'NOT_SUPPORTED')}")
        self._command_renderer().message("\n".join(lines))

    def _cmd_undo(self, arg: str) -> None:
        assert self.executor is not None
        parts = arg.split()
        dry_run = "--dry-run" in parts
        identifiers = [part for part in parts if part != "--dry-run"]
        if len(identifiers) > 1:
            self._command_renderer().message("Usage: /undo [transaction-id] [--dry-run]")
            return
        result = self.executor.undo_transaction(
            identifiers[0] if identifiers else None,
            dry_run=dry_run,
        )
        self._command_renderer().message(result.output or result.error)

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
            self._command_renderer().message("Usage: /txn [list|show <transaction-id>]")
            return
        self._command_renderer().message("\n" + (result.output or result.error))

    def _cmd_session(self, arg: str) -> None:
        manager = getattr(self, "sessions", None)
        if manager is None:
            detail = getattr(self, "session_error", None) or "session persistence is unavailable"
            self._command_renderer().message(f"Sessions unavailable: {detail}")
            return
        parts = arg.strip().split()
        if not parts:
            self._command_renderer().message("\n" + manager.summary_text())
            return
        if parts[0].lower() == "list" and len(parts) == 1:
            self._command_renderer().message("\nSessions:")
            for item in manager.list_sessions():
                marker = "*" if manager.current and item.get("session_id") == manager.current.session_id else "-"
                self._command_renderer().message(
                    f"  {marker} {item.get('session_id', '?')}  {item.get('status', '?')}"
                    f"  tasks={len(item.get('task_ids', []))}"
                    f"  tokens={item.get('total_tokens', 0)}"
                    f"  updated={item.get('updated_at', '?')}"
                )
            return
        if parts[0].lower() == "show" and len(parts) == 2:
            self._command_renderer().message("\n" + manager.summary_text(parts[1]))
            return
        self._command_renderer().message("Usage: /session [list|show <session-id>]")

    def _cmd_trace(self, arg: str) -> None:
        manager = getattr(self, "sessions", None)
        if manager is None:
            detail = getattr(self, "session_error", None) or "session persistence is unavailable"
            self._command_renderer().message(f"Trace unavailable: {detail}")
            return
        task_id = arg.strip()
        if len(task_id.split()) > 1:
            self._command_renderer().message("Usage: /trace [task-id]")
            return
        self._command_renderer().message("\n" + manager.trace_text(task_id=task_id or None))

    def _cmd_projects(self) -> None:
        base = Path(self.cfg["project_dir"]).expanduser()
        base.mkdir(parents=True, exist_ok=True)
        self._command_renderer().message("\nProjects:")
        for p in sorted(base.iterdir()):
            if p.is_dir():
                marker = "*" if p.name == self.current_project else "-"
                self._command_renderer().message(f"  {marker} {p.name}")

    def _cmd_project(self, arg: str) -> None:
        name = arg.strip()
        if not name:
            self._cmd_projects()
            return
        if name.startswith("delete "):
            target_name = name[7:].strip()
            if not target_name or "/" in target_name or "\\" in target_name or target_name in {".", ".."}:
                self._command_renderer().message("Invalid project name.")
                return
            target = Path(self.cfg["project_dir"]).expanduser() / target_name
            if target_name == self.current_project:
                self._command_renderer().message("Switch away before deleting the active project.")
                return
            if self._readline(f"Delete project '{target_name}' permanently? [y/N]: ").strip().lower() == "y":
                shutil.rmtree(target, ignore_errors=True)
                self._command_renderer().message(f"Deleted {target_name}.")
            return
        if "/" in name or "\\" in name or name in {".", ".."}:
            self._command_renderer().message("Project names cannot contain path separators.")
            return
        self._setup_project(name)
        self._command_renderer().message(f"Project: {name}")

    def _cmd_git(self, arg: str) -> None:
        assert self.executor is not None
        parts = arg.strip().split(None, 1)
        sub = parts[0].lower() if parts else "status"
        rest = parts[1] if len(parts) > 1 else ""
        if sub == "init":
            r = self.executor.git_init(rest.strip() or None)
        elif sub == "remote":
            if not rest.strip():
                self._command_renderer().message("Usage: /git remote <url>")
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
            message = rest.strip() or self._readline("Commit message: ").strip()
            r = self.executor.git_commit(message)
        elif sub == "push":
            r = self.executor.git_push(rest.strip())
        elif sub == "pull":
            r = self.executor.git_pull(rest.strip())
        elif sub == "clone":
            clone_parts = rest.split(None, 1)
            if not clone_parts:
                self._command_renderer().message("Usage: /git clone <url> [dest]")
                return
            r = self.executor.git_clone(clone_parts[0], clone_parts[1] if len(clone_parts) > 1 else "")
        elif sub == "stash":
            r = self.executor.git_stash(rest.strip() or "push")
        elif sub == "creds":
            self._command_renderer().message("Sable v2 does not store GitHub PATs. Configure SSH or your normal Git credential helper instead.")
            return
        else:
            self._command_renderer().message("Unknown /git subcommand.")
            return
        self._command_renderer().message(r.output or r.error or "Done.")

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
            elif self._readline(f"Delete '{arg}'? [y/N]: ").strip().lower() == "y":
                r = self.executor.delete_file(arg)
            else:
                self._command_renderer().message("Cancelled.")
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
            self._command_renderer().message(self.executor.current_dir)
            return True
        else:
            return False
        if r is None:
            self._command_renderer().message("Missing or invalid arguments. Type /help.")
        else:
            self._command_renderer().message("\n" + (r.output if r.success else r.error))
        return True
