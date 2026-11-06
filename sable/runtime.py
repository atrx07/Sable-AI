"""Explicit, validated runtime state for one Sable task execution."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

from .config import redact_secrets


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class RuntimeStateError(RuntimeError):
    """Raised when a task attempts an invalid runtime-state transition."""


class RuntimePhase(str, Enum):
    CREATED = "CREATED"
    DISCOVER = "DISCOVER"
    CONTEXT = "CONTEXT"
    PLAN = "PLAN"
    EXECUTE = "EXECUTE"
    VERIFY = "VERIFY"
    REPAIR = "REPAIR"
    REPORT = "REPORT"


class TerminalStatus(str, Enum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    ABORTED = "ABORTED"
    ROLLED_BACK = "ROLLED_BACK"


class TerminationReason(str, Enum):
    SUCCESS = "SUCCESS"
    VERIFICATION_PASSED = "VERIFICATION_PASSED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    VERIFICATION_INCOMPLETE = "VERIFICATION_INCOMPLETE"
    VERIFICATION_BLOCKED = "VERIFICATION_BLOCKED"
    VERIFICATION_TIMEOUT = "VERIFICATION_TIMEOUT"
    REPAIR_NO_PROGRESS = "REPAIR_NO_PROGRESS"
    VERIFICATION_INTEGRITY_BLOCKED = "VERIFICATION_INTEGRITY_BLOCKED"
    TOOL_BUDGET_EXHAUSTED = "TOOL_BUDGET_EXHAUSTED"
    MODEL_TURN_LIMIT = "MODEL_TURN_LIMIT"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    CAPABILITY_DENIED = "CAPABILITY_DENIED"
    BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
    SANDBOX_POLICY_BLOCKED = "SANDBOX_POLICY_BLOCKED"
    PROCESS_TIMEOUT = "PROCESS_TIMEOUT"
    USER_ABORT = "USER_ABORT"
    UNEXPECTED_ERROR = "UNEXPECTED_ERROR"
    ROLLBACK_COMPLETED = "ROLLBACK_COMPLETED"
    ROLLBACK_CONFLICT = "ROLLBACK_CONFLICT"


class RuntimeEventType(str, Enum):
    SESSION_STARTED = "SESSION_STARTED"
    TASK_STARTED = "TASK_STARTED"
    PHASE_CHANGED = "PHASE_CHANGED"
    CONTEXT_SELECTED = "CONTEXT_SELECTED"
    MODEL_REQUEST = "MODEL_REQUEST"
    MODEL_RESPONSE = "MODEL_RESPONSE"
    TOOL_REQUESTED = "TOOL_REQUESTED"
    TOOL_RESULT = "TOOL_RESULT"
    CAPABILITY_REQUESTED = "CAPABILITY_REQUESTED"
    CAPABILITY_APPROVED = "CAPABILITY_APPROVED"
    CAPABILITY_DENIED = "CAPABILITY_DENIED"
    BACKEND_SELECTED = "BACKEND_SELECTED"
    PROCESS_STARTED = "PROCESS_STARTED"
    PROCESS_COMPLETED = "PROCESS_COMPLETED"
    PROCESS_TIMEOUT = "PROCESS_TIMEOUT"
    PROCESS_TERMINATED = "PROCESS_TERMINATED"
    TRANSACTION_STARTED = "TRANSACTION_STARTED"
    FILE_CHANGED = "FILE_CHANGED"
    VERIFICATION_STARTED = "VERIFICATION_STARTED"
    VERIFICATION_RESULT = "VERIFICATION_RESULT"
    VERIFICATION_PLAN_CREATED = "VERIFICATION_PLAN_CREATED"
    VERIFICATION_CHECK_STARTED = "VERIFICATION_CHECK_STARTED"
    VERIFICATION_CHECK_PASSED = "VERIFICATION_CHECK_PASSED"
    VERIFICATION_CHECK_FAILED = "VERIFICATION_CHECK_FAILED"
    VERIFICATION_CHECK_SKIPPED = "VERIFICATION_CHECK_SKIPPED"
    VERIFICATION_SCOPE_ESCALATED = "VERIFICATION_SCOPE_ESCALATED"
    VERIFICATION_BUDGET_EXHAUSTED = "VERIFICATION_BUDGET_EXHAUSTED"
    FAILURE_CLASSIFIED = "FAILURE_CLASSIFIED"
    VERIFICATION_COMPLETED = "VERIFICATION_COMPLETED"
    VERIFICATION_INTEGRITY_WARNING = "VERIFICATION_INTEGRITY_WARNING"
    REPAIR_STARTED = "REPAIR_STARTED"
    REPAIR_REQUESTED = "REPAIR_REQUESTED"
    REPAIR_NO_PROGRESS = "REPAIR_NO_PROGRESS"
    ROLLBACK = "ROLLBACK"
    COMMIT_CREATED = "COMMIT_CREATED"
    TASK_CANCELLED = "TASK_CANCELLED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_FAILED = "TASK_FAILED"


ALLOWED_TRANSITIONS: dict[RuntimePhase, frozenset[RuntimePhase]] = {
    RuntimePhase.CREATED: frozenset({RuntimePhase.DISCOVER}),
    RuntimePhase.DISCOVER: frozenset({RuntimePhase.CONTEXT}),
    RuntimePhase.CONTEXT: frozenset({RuntimePhase.PLAN, RuntimePhase.EXECUTE, RuntimePhase.REPORT}),
    RuntimePhase.PLAN: frozenset({RuntimePhase.EXECUTE, RuntimePhase.REPORT}),
    RuntimePhase.EXECUTE: frozenset({RuntimePhase.VERIFY, RuntimePhase.REPORT}),
    RuntimePhase.VERIFY: frozenset({RuntimePhase.REPAIR, RuntimePhase.REPORT}),
    RuntimePhase.REPAIR: frozenset({RuntimePhase.VERIFY, RuntimePhase.REPORT}),
    RuntimePhase.REPORT: frozenset(),
}


@dataclass(frozen=True)
class RuntimeEvent:
    event_id: str
    timestamp: str
    event_type: RuntimeEventType
    phase: RuntimePhase
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "event_type": self.event_type.value,
            "phase": self.phase.value,
            "metadata": dict(self.metadata),
        }


@dataclass
class RuntimeTask:
    task_id: str
    user_request: str
    workspace_root: str
    created_at: str
    current_phase: RuntimePhase = RuntimePhase.CREATED
    terminal_status: TerminalStatus | None = None
    termination_reason: TerminationReason | None = None
    started_at: str | None = None
    completed_at: str | None = None
    transaction_id: str | None = None
    commit_sha: str | None = None
    session_id: str | None = None
    repository: dict[str, Any] = field(default_factory=dict)
    context_selection: dict[str, Any] = field(default_factory=dict)
    selected_provider: str | None = None
    selected_main_model: str | None = None
    selected_fast_model: str | None = None
    model_turn_count: int = 0
    tool_call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    model_latency_ms: int = 0
    context_duration_ms: int = 0
    routing_purposes: list[str] = field(default_factory=list)
    repair_loop_count: int = 0
    verification: dict[str, Any] = field(default_factory=dict)
    changed_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_ms: int = 0
    events: list[RuntimeEvent] = field(default_factory=list)
    event_handler: Callable[[RuntimeEvent], None] | None = field(
        default=None, repr=False, compare=False
    )
    _started_monotonic: float | None = field(default=None, repr=False, compare=False)

    @classmethod
    def create(
        cls, user_request: str, workspace_root: str, *, session_id: str | None = None
    ) -> "RuntimeTask":
        now = datetime.now(timezone.utc)
        return cls(
            task_id=f"task-{now:%Y%m%d%H%M%S}-{uuid.uuid4().hex[:8]}",
            user_request=redact_secrets(str(user_request))[:2000],
            workspace_root=str(workspace_root),
            created_at=now.isoformat(timespec="microseconds"),
            session_id=session_id,
        )

    @property
    def is_terminal(self) -> bool:
        return self.terminal_status is not None

    @classmethod
    def _safe_event_value(cls, value: Any, depth: int = 0) -> Any:
        if depth >= 3:
            return redact_secrets(str(value))[:500]
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return redact_secrets(value)[:500]
        if isinstance(value, dict):
            return {
                str(key)[:80]: cls._safe_event_value(item, depth + 1)
                for key, item in list(value.items())[:30]
            }
        if isinstance(value, (list, tuple, set)):
            return [cls._safe_event_value(item, depth + 1) for item in list(value)[:50]]
        return redact_secrets(str(value))[:500]

    def _emit(self, event_type: RuntimeEventType, **metadata: Any) -> None:
        safe = {
            str(key)[:80]: self._safe_event_value(value)
            for key, value in metadata.items()
            if value is not None
        }
        event = RuntimeEvent(uuid.uuid4().hex, utc_now(), event_type, self.current_phase, safe)
        self.events.append(event)
        if self.event_handler is not None:
            try:
                self.event_handler(event)
            except Exception:
                # Presentation/observer failure must never alter runtime truth.
                pass

    def emit_event(self, event_type: RuntimeEventType, **metadata: Any) -> None:
        if not isinstance(event_type, RuntimeEventType):
            raise RuntimeStateError(f"Invalid runtime event type: {event_type!r}")
        self._emit(event_type, **metadata)

    def start(self, reason: str = "task_accepted") -> None:
        if self.started_at is not None:
            raise RuntimeStateError("Runtime task has already started.")
        self.started_at = utc_now()
        self._started_monotonic = time.monotonic()
        self._emit(RuntimeEventType.TASK_STARTED, reason=reason)
        self.transition(RuntimePhase.DISCOVER, reason="runtime_started")

    def transition(self, phase: RuntimePhase, *, reason: str) -> None:
        if self.is_terminal:
            raise RuntimeStateError(f"Terminal task {self.task_id} cannot transition.")
        if not isinstance(phase, RuntimePhase):
            raise RuntimeStateError(f"Invalid runtime phase: {phase!r}")
        allowed = ALLOWED_TRANSITIONS[self.current_phase]
        if phase not in allowed:
            raise RuntimeStateError(
                f"Invalid runtime transition: {self.current_phase.value} -> {phase.value}"
            )
        previous = self.current_phase
        self.current_phase = phase
        self._emit(
            RuntimeEventType.PHASE_CHANGED,
            **{"from": previous.value, "to": phase.value, "reason": reason},
        )

    def terminate(
        self,
        status: TerminalStatus,
        reason: TerminationReason,
        *,
        error: str | None = None,
        allow_from_active_phase: bool = False,
    ) -> None:
        if self.is_terminal:
            raise RuntimeStateError(f"Terminal task {self.task_id} is immutable.")
        if self.current_phase != RuntimePhase.REPORT and not allow_from_active_phase:
            raise RuntimeStateError("Normal task termination is only valid from REPORT.")
        self.terminal_status = status
        self.termination_reason = reason
        self.completed_at = utc_now()
        if self._started_monotonic is not None:
            self.duration_ms = max(0, int((time.monotonic() - self._started_monotonic) * 1000))
        if error:
            self.errors.append(redact_secrets(str(error))[:1000])
        event_type = (
            RuntimeEventType.TASK_COMPLETED
            if status == TerminalStatus.COMPLETED
            else RuntimeEventType.TASK_FAILED
        )
        self._emit(event_type, status=status.value, reason=reason.value, error=error)

    def record_agent_result(self, result: dict[str, Any]) -> None:
        self.model_turn_count += int(result.get("model_calls", result.get("steps", 0)) or 0)
        self.tool_call_count += int(result.get("tool_calls", 0) or 0)
        usage = (
            result.get("model_usage", {}) if isinstance(result.get("model_usage", {}), dict) else {}
        )
        self.input_tokens += int(usage.get("input_tokens", 0) or 0)
        self.output_tokens += int(usage.get("output_tokens", 0) or 0)
        self.total_tokens += int(usage.get("total_tokens", 0) or 0)
        self.model_latency_ms += int(result.get("model_latency_ms", 0) or 0)
        self.routing_purposes.extend(str(item) for item in result.get("routing_purposes", []))
        selection = result.get("context_selection", {})
        if isinstance(selection, dict):
            self.context_selection = dict(selection)
            self.context_duration_ms += int(selection.get("duration_ms", 0) or 0)
        self.changed_files = list(
            dict.fromkeys(self.changed_files + list(result.get("changed_files", [])))
        )

    def to_dict(self, *, include_events: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "task_id": self.task_id,
            "user_request": self.user_request,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "current_phase": self.current_phase.value,
            "terminal_status": self.terminal_status.value if self.terminal_status else None,
            "termination_reason": self.termination_reason.value
            if self.termination_reason
            else None,
            "transaction_id": self.transaction_id,
            "commit_sha": self.commit_sha,
            "session_id": self.session_id,
            "workspace_root": self.workspace_root,
            "repository": dict(self.repository),
            "context_selection": dict(self.context_selection),
            "selected_provider": self.selected_provider,
            "selected_main_model": self.selected_main_model,
            "selected_fast_model": self.selected_fast_model,
            "model_turn_count": self.model_turn_count,
            "tool_call_count": self.tool_call_count,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "model_latency_ms": self.model_latency_ms,
            "context_duration_ms": self.context_duration_ms,
            "routing_purposes": list(self.routing_purposes),
            "repair_loop_count": self.repair_loop_count,
            "verification": dict(self.verification),
            "changed_files": list(self.changed_files),
            "termination_reason_detail": list(self.errors),
            "duration_ms": self.duration_ms,
        }
        if include_events:
            data["events"] = [event.to_dict() for event in self.events]
        return data


def request_needs_plan(user_request: str) -> bool:
    """Skip the explicit plan phase only for bounded read-only inspection intents."""
    normalized = " ".join(str(user_request).lower().split())
    trivial_prefixes = (
        "read ",
        "show ",
        "list ",
        "inspect ",
        "check git status",
        "git status",
        "what is in ",
        "find ",
        "search ",
    )
    return not any(normalized.startswith(prefix) for prefix in trivial_prefixes)


def capability_ceiling_text(mode: str) -> str:
    """One-line statement of what the current mode forbids, for evidence blocks."""
    normalized = mode if mode in {"plan", "build", "yolo"} else "build"
    if normalized == "plan":
        return (
            "plan mode is read-only: no file edits, commits, or pushes; "
            "git_status/git_log/git_diff are allowed"
        )
    if normalized == "build":
        return (
            "build mode: workspace edits allowed; git_push/git_commit/git_pull are "
            "not requestable in build mode — use /mode yolo plus human approval, "
            "or run /git push yourself"
        )
    return (
        "yolo mode: git_push/git_commit need human approval; "
        "the model cannot approve its own requests"
    )


def _check_status_category(item: Any) -> tuple[str, str]:
    """Return (status, category) strings for a verification check, dict or object form."""
    if isinstance(item, dict):
        check = item.get("check", {})
        category = check.get("category", "") if isinstance(check, dict) else ""
        return str(item.get("status", "")).upper(), str(category).upper()
    status = getattr(item, "status", "")
    check = getattr(item, "check", None)
    category = getattr(check, "category", "") if check is not None else ""
    return (
        str(getattr(status, "value", status)).upper(),
        str(getattr(category, "value", category)).upper(),
    )


def _safe_line(value: Any, limit: int = 200) -> str:
    """Collapse a runtime value to one sanitised line for evidence blocks.

    Untrusted values (tool errors, filenames, branch names, commit SHAs)
    may contain newlines or control characters that would forge extra
    ``key: value`` lines inside a runtime-owned block. Redact secrets,
    replace non-printable characters with spaces, collapse whitespace
    runs, and truncate. The result is always a single line.
    """
    text = redact_secrets("" if value is None else str(value))
    text = "".join(ch if ch.isprintable() else " " for ch in text)
    return " ".join(text.split())[:limit]


def _check_name(item: Any) -> str:
    if isinstance(item, dict):
        check = item.get("check", {})
        if isinstance(check, dict):
            return _safe_line(check.get("name", item.get("name", "check")), 160)
        return _safe_line(item.get("name", "check"), 160)
    return _safe_line(getattr(item, "name", "check"), 160)


def verification_depth(verification: dict[str, Any] | None) -> str:
    """Coarse depth of what a verification run actually demonstrated.

    One of SYNTAX_ONLY | UNIT_TESTED | FULL | NONE, computed from the
    categories of the checks that ran — never from model text, and
    regardless of the overall outcome: a failed syntax check is
    SYNTAX_ONLY, a failed unit-test check is UNIT_TESTED. The outcome
    itself (PASS/FAIL/SKIPPED) is reported separately; depth says only
    what the verification covered. No checks, only skipped checks, or a
    SKIPPED run means NONE. A PASS whose checks are only
    TYPECHECK/BUILD/etc. is also SYNTAX_ONLY: the label must never imply
    functional verification that did not run. The appended runtime line
    names the checks that actually ran.
    """
    if not isinstance(verification, dict):
        return "NONE"
    checks = verification.get("checks") or []
    if not checks:
        return "NONE"
    overall = str(verification.get("overall_status") or verification.get("status") or "").upper()
    if overall == "SKIPPED":
        return "NONE"
    ran_categories: set[str] = set()
    for item in checks:
        status, category = _check_status_category(item)
        if category and not status.startswith("SKIPPED"):
            ran_categories.add(category)
    if not ran_categories:
        return "NONE"
    if ran_categories & {"UNIT_TEST", "INTEGRATION_TEST"}:
        if str(verification.get("scope", "") or "").upper() == "FULL":
            return "FULL"
        return "UNIT_TESTED"
    return "SYNTAX_ONLY"


def _format_paths(paths: Any, *, limit: int = 5) -> str:
    items = [_safe_line(path) for path in list(paths or [])]
    if not items:
        return "[]"
    shown = items[:limit]
    suffix = f" (+{len(items) - limit} more)" if len(items) > limit else ""
    return "[" + ", ".join(shown) + "]" + suffix


def build_evidence_summary(
    *,
    mode: str = "build",
    tool_results: list[Any] | None = None,
    changed_files: list[str] | None = None,
    verification: dict[str, Any] | None = None,
    git: dict[str, Any] | None = None,
    git_baseline: dict[str, Any] | None = None,
    commit_sha: str | None = None,
    git_commit: str | None = None,
    git_push: str | None = None,
    previous_task: str | None = None,
) -> str:
    """Build a runtime-owned evidence block from runtime records only.

    Pure function: plain data in, redacted string out. Model text is never an
    input, so model output cannot mutate the block. The block is injected as
    a system message the model must treat as fact; the same facts are what
    the renderer shows the user.
    """
    lines = [
        "RUNTIME EVIDENCE — deterministic runtime facts, not model claims. "
        "State about files, git, verification, or external actions must come "
        "from this block; if it does not establish a claim, say so or call "
        "the inspection tool."
    ]
    lines.append(f"mode: {_safe_line(mode, 40)} ({capability_ceiling_text(mode)})")
    executed: list[str] = []
    denials: list[str] = []
    for item in tool_results or []:
        if isinstance(item, dict):
            name = str(item.get("tool", "?"))
            ok = bool(item.get("success", False))
            error = str(item.get("error", "") or "")
        else:
            name = str(getattr(item, "tool", "?"))
            ok = bool(getattr(item, "success", False))
            error = str(getattr(item, "error", "") or "")
        executed.append(f"{_safe_line(name, 80)}({'ok' if ok else 'failed'})")
        if not ok and error:
            denials.append(f"{_safe_line(name, 80)}: {_safe_line(error)}")
    lines.append(f"tools executed this turn: {', '.join(executed) if executed else 'none'}")
    if denials:
        lines.append("denied/failed tool calls: " + "; ".join(denials[:5]))
    files = [_safe_line(path) for path in dict.fromkeys(changed_files or [])]
    files_text = ", ".join(files[:10]) if files else "none"
    if len(files) > 10:
        files_text += f" (+{len(files) - 10} more)"
    lines.append(f"files changed this turn: {files_text}")
    state = git if isinstance(git, dict) and git.get("present") else None
    if state is None and isinstance(git_baseline, dict) and git_baseline.get("repo_root"):
        state = git_baseline
    if state is not None:
        branch = _safe_line(state.get("branch") or "(detached HEAD)", 120)
        head = _safe_line(state.get("head") or "", 12)
        git_line = f"git: branch: {branch}"
        if head:
            git_line += f", HEAD: {head}"
        git_line += f", staged: {_format_paths(state.get('staged'))}"
        git_line += f", unstaged: {_format_paths(state.get('unstaged'))}"
        git_line += f", untracked: {_format_paths(state.get('untracked'))}"
        if state.get("remote") is not None:
            git_line += (
                ", remote: configured"
                if state.get("remote")
                else ", remote: no remote named 'origin' configured"
            )
        if state.get("upstream") and state.get("ahead") is not None:
            git_line += f", ahead: {int(state.get('ahead') or 0)}"
        if state is git_baseline:
            git_line += " (at task start)"
        lines.append(git_line)
    else:
        lines.append("git: no repository state available")
    if verification is None:
        lines.append("verification: not run yet in this task")
    else:
        depth = verification_depth(verification)
        overall = _safe_line(
            verification.get("overall_status") or verification.get("status") or "unknown",
            40,
        ).upper()
        scope = _safe_line(verification.get("scope", "") or "unknown", 40)
        check_bits: list[str] = []
        for item in verification.get("checks") or []:
            status, _category = _check_status_category(item)
            check_bits.append(f"{_check_name(item)} ({_safe_line(status, 40)})")
        detail = f"scope {scope}; depth {depth}"
        if check_bits:
            detail += "; checks: " + ", ".join(check_bits[:10])
        if depth == "SYNTAX_ONLY":
            detail += " — syntax only; no tests ran; functionality not demonstrated"
        lines.append(f"verification: {overall} ({detail})")
    if commit_sha:
        lines.append(f"runtime git actions: auto-committed as {_safe_line(commit_sha, 40)}")
    elif git_commit:
        lines.append(f"runtime git actions: {_safe_line(git_commit, 300)}")
    else:
        lines.append("runtime git actions: none")
    if git_push:
        push_text = (
            "No remote named 'origin' configured"
            if git_push == "__NEEDS_REMOTE__"
            else _safe_line(git_push, 300)
        )
        lines.append(f"runtime push: {push_text}")
    if previous_task:
        lines.append(f"previous task: {_safe_line(previous_task, 1000)}")
    return "\n".join(lines)


def build_history_digest(
    *,
    changed_files: list[str] | None = None,
    verification: dict[str, Any] | None = None,
    git: dict[str, Any] | None = None,
    commit_sha: str | None = None,
    git_commit: str | None = None,
    mode: str = "build",
) -> str:
    """One-line runtime-owned digest for conversation history.

    Appended as a system message after each turn so the next turn's model
    sees runtime facts — files changed, verification depth, git state, commit
    outcome — instead of only the previous turn's prose. Explicitly a record
    of the past turn, never a claim about current state.
    """
    parts: list[str] = []
    files = [_safe_line(path) for path in dict.fromkeys(changed_files or [])]
    parts.append("files changed: " + (", ".join(files[:10]) if files else "none"))
    if verification is None:
        parts.append("verification: not run yet")
    else:
        overall = _safe_line(
            verification.get("overall_status") or verification.get("status") or "unknown",
            40,
        ).upper()
        parts.append(f"verification: {overall} (depth {verification_depth(verification)})")
    if isinstance(git, dict) and git.get("present"):
        branch = _safe_line(git.get("branch") or "(detached HEAD)", 120)
        head = _safe_line(git.get("head") or "", 12)
        staged = len(git.get("staged") or [])
        git_part = f"git: branch {branch}"
        if head:
            git_part += f" HEAD {head}"
        git_part += f", staged {staged}"
        parts.append(git_part)
    if commit_sha:
        parts.append(f"committed as {_safe_line(commit_sha, 12)}")
    elif git_commit:
        parts.append(f"git: {_safe_line(git_commit)}")
    else:
        parts.append("not committed")
    parts.append(f"mode: {_safe_line(mode, 40)}")
    return (
        "[RUNTIME DIGEST] "
        + "; ".join(parts)
        + ". Recorded state from a previous turn — re-inspect before asserting current state."
    )


def build_failure_report(
    *,
    kind: str,
    error: str,
    tool_results: list[Any] | None = None,
    changed_files: list[str] | None = None,
) -> str:
    """Runtime-owned report for a run stopped by a provider/interrupt failure.

    Names what was done, what was not done, and what remains reversible —
    never a bare error line and never a misleading "Done."
    """
    done: list[str] = []
    for result in list(tool_results or [])[:10]:
        name = getattr(result, "tool", None) or "tool"
        outcome = "ok" if getattr(result, "success", False) else "failed"
        done.append(f"{_safe_line(name, 80)}({outcome})")
    files = [_safe_line(path) for path in dict.fromkeys(changed_files or [])]
    cause = "interrupted by the user" if kind == "interrupted" else "the provider failed"
    detail = _safe_line(error, 300) or "no further detail"
    lines = [
        f"[RUNTIME] The run stopped before finishing: {cause} — {detail}.",
        "Done: "
        + (
            f"tool calls {', '.join(done)}; files changed: {', '.join(files)}"
            if done or files
            else "nothing — no tool call completed before the failure"
        ),
        "Not done: the task is incomplete — no final answer was produced.",
    ]
    if files:
        lines.append(
            "Reversible: the file changes above are uncommitted working-tree edits; "
            "they were not auto-committed and remain reversible with /undo."
        )
    else:
        lines.append("Reversible: nothing was changed, so there is nothing to roll back.")
    return "\n".join(lines)


# Git tools that attempt a mutation. A turn that called none of these gets
# the mode ceiling in its outcome line, so the reader knows why nothing
# was committed or pushed.
_MUTATING_GIT_TOOLS = frozenset({"git_commit", "git_push", "git_pull", "git_add"})


def runtime_outcome_line(
    *,
    verification: dict[str, Any] | None = None,
    commit_sha: str | None = None,
    git_commit: str | None = None,
    git_push: str | None = None,
    git: dict[str, Any] | None = None,
    mode: str = "build",
    tool_results: list[Any] | None = None,
) -> str:
    """One runtime-owned outcome line appended to chat_reply after verify/git.

    States what the runtime actually did and what verification actually
    demonstrated, so the model's pre-verification prose never stands alone.
    When ``tool_results`` is given and the turn attempted no mutating git
    tool, the applicable mode ceiling is appended as a ``Policy:`` part,
    so a zero-git-tool turn states why nothing was committed or pushed
    instead of leaving the reason to inference.
    """
    parts: list[str] = []
    if verification is None:
        parts.append("Verification: not run")
    else:
        overall = _safe_line(
            verification.get("overall_status") or verification.get("status") or "unknown",
            40,
        ).upper()
        if overall == "SKIPPED":
            parts.append("Verification: not run")
        else:
            depth = verification_depth(verification)
            scope = _safe_line(verification.get("scope", "") or "unknown", 40)
            check_bits: list[str] = []
            for item in verification.get("checks") or []:
                status, _category = _check_status_category(item)
                check_bits.append(f"{_check_name(item)} ({_safe_line(status, 40)})")
            detail = f"scope {scope}; depth {depth}"
            if check_bits:
                detail += "; checks: " + ", ".join(check_bits[:10])
            if depth == "SYNTAX_ONLY":
                detail += " — syntax only; no tests ran; functionality not demonstrated"
            parts.append(f"Verification: {overall} ({detail})")
    if commit_sha:
        parts.append(f"Git: auto-committed as {_safe_line(commit_sha, 12)}")
    elif git_commit:
        parts.append(f"Git: {_safe_line(git_commit)}")
    elif isinstance(git, dict) and git.get("present"):
        branch = _safe_line(git.get("branch") or "detached HEAD", 120)
        parts.append(
            f"Git: no commit created (branch: {branch}, staged: {_format_paths(git.get('staged'))})"
        )
    if git_push:
        push_text = (
            "No remote named 'origin' configured"
            if git_push == "__NEEDS_REMOTE__"
            else _safe_line(git_push, 300)
        )
        parts.append(f"Publish: {push_text}")
    if tool_results is not None:
        called: set[str] = set()
        for item in tool_results:
            if isinstance(item, dict):
                called.add(str(item.get("tool", "")))
            else:
                called.add(str(getattr(item, "tool", "")))
        if not called & _MUTATING_GIT_TOOLS:
            parts.append(f"Policy: {capability_ceiling_text(mode)}")
    return "[RUNTIME] " + " · ".join(parts)
