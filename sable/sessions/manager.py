"""Bounded local session persistence and redacted JSONL event tracing."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import CONFIG_DIR, redact_secrets
from ..runtime import RuntimeEvent, RuntimeTask, utc_now
from .models import SessionRecord, SessionStatus, TraceEvent


class SessionError(RuntimeError):
    pass


class SessionManager:
    def __init__(
        self,
        workspace: str | Path,
        *,
        provider: str = "groq",
        main_model: str = "unknown",
        fast_model: str = "unknown",
        storage_dir: str | Path | None = None,
        max_sessions: int = 20,
        max_events: int = 2000,
        max_trace_bytes: int = 4 * 1024 * 1024,
    ):
        self.workspace = Path(workspace).expanduser().resolve()
        key = hashlib.sha256(str(self.workspace).encode("utf-8")).hexdigest()[:20]
        self._workspace_key = key
        self._default_storage = storage_dir is None
        self.storage_root = Path(storage_dir or (CONFIG_DIR / "sessions" / key)).expanduser()
        self.provider = str(provider)
        self.main_model = str(main_model)
        self.fast_model = str(fast_model)
        self.max_sessions = max(1, int(max_sessions))
        self.max_events = max(10, int(max_events))
        self.max_trace_bytes = max(4096, int(max_trace_bytes))
        self.sessions: list[SessionRecord] = []
        self.current: SessionRecord | None = None
        self._prepare_storage()
        self._load()
        self._resume_or_create()

    def _prepare_storage(self) -> None:
        try:
            self.storage_root.mkdir(parents=True, exist_ok=True)
        except OSError:
            if not self._default_storage:
                raise
            self.storage_root = Path(tempfile.gettempdir()) / "sable-sessions" / self._workspace_key
            self.storage_root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.storage_root, 0o700)
        except OSError:
            pass

    def _load(self) -> None:
        loaded: list[SessionRecord] = []
        for metadata in self.storage_root.glob("*/metadata.json"):
            try:
                raw = json.loads(metadata.read_text(encoding="utf-8"))
                session = SessionRecord.from_dict(raw)
                if Path(session.workspace).resolve() == self.workspace:
                    loaded.append(session)
            except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError):
                continue
        self.sessions = sorted(loaded, key=lambda item: (item.updated_at, item.session_id))
        self._prune()

    def _resume_or_create(self) -> None:
        active = [item for item in self.sessions if item.status == SessionStatus.ACTIVE.value]
        if active:
            self.current = active[-1]
            self.current.provider = self.provider
            self.current.main_model = self.main_model
            self.current.fast_model = self.fast_model
            self.current.updated_at = utc_now()
            self._save(self.current)
            return
        now = datetime.now(timezone.utc)
        session = SessionRecord(
            session_id=f"session-{now:%Y%m%d%H%M%S}-{uuid.uuid4().hex[:8]}",
            created_at=now.isoformat(timespec="microseconds"),
            updated_at=now.isoformat(timespec="microseconds"),
            workspace=str(self.workspace),
            provider=self.provider,
            main_model=self.main_model,
            fast_model=self.fast_model,
        )
        self.sessions.append(session)
        self.current = session
        self._save(session)
        self._append_trace_events(session, [TraceEvent(
            event_id=uuid.uuid4().hex,
            timestamp=utc_now(),
            session_id=session.session_id,
            task_id=None,
            transaction_id=None,
            event_type="SESSION_STARTED",
            phase=None,
            metadata={"workspace": str(self.workspace), "provider": self.provider},
        )])
        self._prune()

    def start_new_session(self) -> SessionRecord:
        if self.current is not None:
            self.current.status = SessionStatus.CLOSED.value
            self.current.updated_at = utc_now()
            self._save(self.current)
        self.current = None
        self._resume_or_create()
        assert self.current is not None
        return self.current

    def _directory(self, session: SessionRecord) -> Path:
        return self.storage_root / session.session_id

    def _atomic_json(self, path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}-", suffix=".tmp", dir=str(path.parent), text=True)
        temp = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        except Exception:
            temp.unlink(missing_ok=True)
            raise

    def _save(self, session: SessionRecord) -> None:
        directory = self._directory(session)
        directory.mkdir(parents=True, exist_ok=True)
        self._atomic_json(directory / "metadata.json", session.to_dict())

    @classmethod
    def _safe_value(cls, value: Any, depth: int = 0) -> Any:
        if depth >= 4:
            return redact_secrets(str(value))[:500]
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return redact_secrets(value)[:1000]
        if isinstance(value, dict):
            return {str(key)[:80]: cls._safe_value(item, depth + 1) for key, item in list(value.items())[:50]}
        if isinstance(value, (list, tuple, set)):
            return [cls._safe_value(item, depth + 1) for item in list(value)[:100]]
        return redact_secrets(str(value))[:500]

    def _append_trace_events(self, session: SessionRecord, events: list[TraceEvent]) -> None:
        if not events:
            return
        trace_path = self._directory(session) / "events.jsonl"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        with trace_path.open("a", encoding="utf-8") as handle:
            for event in events:
                safe = event.to_dict()
                safe["metadata"] = self._safe_value(safe.get("metadata", {}))
                line = json.dumps(safe, sort_keys=True, ensure_ascii=False)
                if len(line) > 5000:
                    safe["metadata"] = {"truncated": True}
                    line = json.dumps(safe, sort_keys=True)
                handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._bound_trace(trace_path)

    def _bound_trace(self, trace_path: Path) -> None:
        try:
            if trace_path.stat().st_size <= self.max_trace_bytes:
                lines = trace_path.read_text(encoding="utf-8", errors="replace").splitlines()
                if len(lines) <= self.max_events:
                    return
            else:
                lines = trace_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return
        kept: list[str] = []
        total = 0
        for line in reversed(lines[-self.max_events:]):
            size = len(line.encode("utf-8")) + 1
            if kept and total + size > self.max_trace_bytes:
                break
            kept.append(line)
            total += size
        kept.reverse()
        temp = trace_path.with_suffix(".jsonl.tmp")
        temp.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        os.replace(temp, trace_path)

    def record_task(self, task: RuntimeTask) -> None:
        if self.current is None:
            raise SessionError("No active Sable session.")
        session = self.current
        task.session_id = session.session_id
        trace_events = [self._trace_event(session, task, event) for event in task.events]
        self._append_trace_events(session, trace_events)
        public_task = self._safe_value(task.to_dict(include_events=False))
        self._atomic_json(self._directory(session) / "tasks" / f"{task.task_id}.json", public_task)
        if task.task_id not in session.task_ids:
            session.task_ids.append(task.task_id)
            session.total_model_calls += task.model_turn_count
            session.total_tool_calls += task.tool_call_count
            session.input_tokens += task.input_tokens
            session.output_tokens += task.output_tokens
            session.total_tokens += task.total_tokens
            session.total_duration_ms += task.duration_ms
        session.latest_task = {
            "task_id": task.task_id,
            "request": task.user_request[:240],
            "status": task.terminal_status.value if task.terminal_status else None,
            "termination_reason": task.termination_reason.value if task.termination_reason else None,
            "verification": dict(task.verification),
            "files_changed": len(task.changed_files),
            "transaction_id": task.transaction_id,
        }
        session.updated_at = utc_now()
        session.task_ids = session.task_ids[-500:]
        task_dir = self._directory(session) / "tasks"
        retained = set(session.task_ids)
        for path in task_dir.glob("*.json"):
            if path.stem not in retained:
                path.unlink(missing_ok=True)
        self._save(session)

    @staticmethod
    def _trace_event(session: SessionRecord, task: RuntimeTask, event: RuntimeEvent) -> TraceEvent:
        return TraceEvent(
            event_id=event.event_id,
            timestamp=event.timestamp,
            session_id=session.session_id,
            task_id=task.task_id,
            transaction_id=task.transaction_id,
            event_type=event.event_type.value,
            phase=event.phase.value,
            metadata=dict(event.metadata),
        )

    def get(self, session_id: str) -> SessionRecord | None:
        exact = [item for item in self.sessions if item.session_id == session_id]
        if exact:
            return exact[-1]
        prefix = [item for item in self.sessions if item.session_id.startswith(session_id)]
        return prefix[0] if len(prefix) == 1 else None

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        return [item.to_dict() for item in reversed(self.sessions[-max(1, min(50, int(limit))):])]

    def read_task(self, task_id: str, session_id: str | None = None) -> dict[str, Any] | None:
        sessions = [self.get(session_id)] if session_id else list(reversed(self.sessions))
        for session in sessions:
            if session is None:
                continue
            path = self._directory(session) / "tasks" / f"{task_id}.json"
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    return value
            except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError):
                continue
        return None

    def trace(self, *, session_id: str | None = None, task_id: str | None = None, limit: int = 50) -> list[TraceEvent]:
        session = self.get(session_id) if session_id else self.current
        if session is None:
            return []
        path = self._directory(session) / "events.jsonl"
        events: list[TraceEvent] = []
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    raw = json.loads(line)
                    event = TraceEvent.from_dict(raw)
                except (ValueError, TypeError, AttributeError, json.JSONDecodeError):
                    continue
                if task_id and event.task_id != task_id:
                    continue
                events.append(event)
        except OSError:
            return []
        return events[-max(1, min(200, int(limit))):]

    def summary_text(self, session_id: str | None = None) -> str:
        session = self.get(session_id) if session_id else self.current
        if session is None:
            return "No Sable session is available."
        latest = session.latest_task or {}
        lines = [
            f"SABLE SESSION {session.session_id}",
            f"Status          {session.status}",
            f"Workspace       {session.workspace}",
            f"Provider        {session.provider}",
            f"Main model      {session.main_model}",
            f"Fast model      {session.fast_model}",
            f"Tasks           {len(session.task_ids)}",
            f"Model calls     {session.total_model_calls}",
            f"Tool calls      {session.total_tool_calls}",
            f"Tokens          {session.total_tokens}",
            f"Duration        {session.total_duration_ms / 1000:.2f}s",
        ]
        if latest:
            lines.extend([
                "Latest task:",
                f"  {latest.get('request', '')}",
                f"  Status        {latest.get('status') or 'unknown'}",
                f"  Termination   {latest.get('termination_reason') or 'unknown'}",
                f"  Files changed {latest.get('files_changed', 0)}",
                f"  Transaction   {latest.get('transaction_id') or 'none'}",
            ])
        return "\n".join(lines)

    def trace_text(self, *, task_id: str | None = None, limit: int = 40) -> str:
        events = self.trace(task_id=task_id, limit=limit)
        if not events:
            return "No trace events are available."
        lines = []
        for event in events:
            detail = event.metadata.get("reason") or event.metadata.get("tool") or event.metadata.get("status") or ""
            lines.append(f"{event.timestamp}  {event.event_type:<22} {event.phase or '-':<8} {str(detail)[:100]}")
        return "\n".join(lines)

    def _prune(self) -> None:
        self.sessions.sort(key=lambda item: (item.updated_at, item.session_id))
        while len(self.sessions) > self.max_sessions:
            victim = self.sessions.pop(0)
            shutil.rmtree(self._directory(victim), ignore_errors=True)
