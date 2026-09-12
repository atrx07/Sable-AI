"""Terminal presentation for structured Sable runtime truth.

Renderers in this module format existing events and result dictionaries. They
must not infer success, grant capabilities, or alter runtime state.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import textwrap
from typing import Any, Callable, TextIO

from .capabilities import ApprovalDecision, CapabilityRequest
from .config import redact_secrets
from .runtime import RuntimeEvent, RuntimeEventType


ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "cyan": "\033[36m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "red": "\033[31m",
}

ANSI_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _enum_text(value: Any) -> str:
    return str(getattr(value, "value", value))


def _safe(value: Any, limit: int = 500) -> str:
    return redact_secrets("" if value is None else str(value))[:limit]


class PlainRenderer:
    """Stable, bounded, non-ANSI terminal output."""

    def __init__(
        self,
        *,
        stream: TextIO | None = None,
        error_stream: TextIO | None = None,
        width: int | None = None,
        quiet: bool = False,
        verbose: bool = False,
        input_func: Callable[[str], str] | None = None,
    ):
        self.stream = stream or sys.stdout
        self.error_stream = error_stream or sys.stderr
        self.width = max(28, int(width or shutil.get_terminal_size((88, 24)).columns))
        self.quiet = bool(quiet)
        self.verbose = bool(verbose)
        self.input_func = input_func or input
        self._last_phase: str | None = None

    @property
    def color_enabled(self) -> bool:
        return False

    @property
    def unicode_enabled(self) -> bool:
        encoding = str(getattr(self.stream, "encoding", "") or "").lower()
        return "utf" in encoding

    def _write(self, text: str = "", *, error: bool = False) -> None:
        print(ANSI_PATTERN.sub("", redact_secrets(text)), file=self.error_stream if error else self.stream)

    def _styled(self, text: str, _style: str) -> str:
        return text

    def _wrap(self, text: Any, *, indent: str = "  ", subsequent: str | None = None) -> list[str]:
        clean = _safe(text, 4000).replace("\t", "  ")
        available = max(12, self.width - len(indent))
        lines: list[str] = []
        for source in clean.splitlines() or [""]:
            wrapped = textwrap.wrap(
                source,
                width=available,
                break_long_words=True,
                break_on_hyphens=False,
                replace_whitespace=False,
            ) or [""]
            for index, part in enumerate(wrapped):
                lines.append((indent if index == 0 else (subsequent or indent)) + part)
        return lines

    def _section(self, title: str) -> None:
        self._write()
        self._write(self._styled(title, "bold"))

    def render_startup(self, info: dict[str, Any]) -> None:
        if self.quiet:
            return
        self._write(self._styled("SABLE", "bold"))
        for label, key in (
            ("Workspace", "workspace"),
            ("Provider", "provider"),
            ("Model", "model"),
            ("Backend", "backend"),
            ("Mode", "mode"),
            ("Verification", "verification"),
        ):
            if info.get(key) not in (None, ""):
                self._write(f"{label:<13}{_safe(info[key], 300)}")

    def status(self, message: str) -> None:
        if not self.quiet:
            self._write(f"[status] {_safe(message, 300)}", error=True)

    def message(self, message: str = "", *, error: bool = False) -> None:
        if not self.quiet:
            self._write(_safe(message, 12000), error=error)

    def render_fields(self, title: str, rows: list[tuple[str, Any]]) -> None:
        if self.quiet:
            return
        self._write(self._styled(title, "bold"))
        for label, value in rows:
            self._write(f"{label:<15}{_safe(value, 1000)}")

    def on_event(self, event: RuntimeEvent) -> None:
        """Render a compact live update from one already-recorded runtime event."""
        if self.quiet:
            return
        event_type = event.event_type
        meta = event.metadata
        if event_type == RuntimeEventType.PHASE_CHANGED:
            phase = _safe(meta.get("to", event.phase.value), 20).upper()
            if phase != self._last_phase:
                self._last_phase = phase
                self._write(f"[phase] {phase.title()}", error=True)
        elif event_type == RuntimeEventType.CONTEXT_SELECTED:
            selected = int(meta.get("files_selected", 0) or 0)
            considered = int(meta.get("files_considered", 0) or 0)
            self._write(f"[context] {selected} files selected from {considered}", error=True)
            if self.verbose:
                for item in list(meta.get("items", []))[:12]:
                    path = _safe(_value(item, "path", "unknown"), 300)
                    reasons = ", ".join(_safe(reason, 120) for reason in list(_value(item, "reasons", []))[:3])
                    self._write(f"  {path}" + (f" - {reasons}" if reasons else ""), error=True)
        elif event_type == RuntimeEventType.TOOL_RESULT:
            ok = bool(meta.get("success"))
            label = "OK" if ok else "FAIL"
            tool = _safe(meta.get("tool", "tool"), 80)
            changed = list(meta.get("changed_files", []))
            suffix = f" - {len(changed)} file(s) changed" if changed else ""
            self._write(f"[tool {label}] {tool}{suffix}", error=True)
            if not ok and meta.get("error_excerpt"):
                for line in self._wrap(meta["error_excerpt"], indent="  ")[:3]:
                    self._write(line, error=True)
        elif event_type in {
            RuntimeEventType.VERIFICATION_CHECK_PASSED,
            RuntimeEventType.VERIFICATION_CHECK_FAILED,
            RuntimeEventType.VERIFICATION_CHECK_SKIPPED,
        }:
            status = _safe(meta.get("status", "UNKNOWN"), 40).upper()
            name = _safe(meta.get("name", "check"), 160)
            self._write(f"[verify {status}] {name}", error=True)
        elif event_type == RuntimeEventType.REPAIR_STARTED:
            self._write(f"[repair] Attempt {int(meta.get('loop', 0) or 0)}", error=True)
        elif event_type == RuntimeEventType.ROLLBACK:
            label = "OK" if meta.get("success") else "CONFLICT"
            self._write(f"[rollback {label}] transaction recovery", error=True)

    def prompt_approval(self, request: CapabilityRequest) -> ApprovalDecision:
        """Collect an explicit decision. Blank, EOF, and Ctrl+C always deny."""
        self._write()
        self._write("Elevated capability requested")
        rows = (
            ("Capability", _enum_text(request.capability)),
            ("Action", request.action),
            ("Source", _enum_text(request.source)),
            ("Risk", str(request.risk).upper()),
            ("Tool", request.tool),
        )
        for label, value in rows:
            self._write(f"{label:<13}{_safe(value, 500)}")
        if request.reason:
            self._write("Reason")
            for line in self._wrap(request.reason):
                self._write(line)
        self._write("[A] Allow once  [S] Allow exact action for session  [D] Deny")
        while True:
            try:
                choice = self._read_decision().strip().lower()
            except (EOFError, KeyboardInterrupt):
                self._write("Denied.")
                return ApprovalDecision.DENY
            if choice in {"a", "allow", "once"}:
                return ApprovalDecision.ALLOW_ONCE
            if choice in {"s", "session"}:
                return ApprovalDecision.ALLOW_SESSION
            if choice in {"", "d", "deny", "no", "n"}:
                return ApprovalDecision.DENY
            self._write("Choose A, S, or D. The request remains denied by default.")

    def _read_decision(self) -> str:
        return self.input_func("Decision [D]: ")

    def _render_tool_results(self, tools: list[Any]) -> None:
        if not tools:
            return
        self._section("Tools")
        for item in tools:
            ok = bool(_value(item, "success", False))
            tool = _safe(_value(item, "tool", "tool"), 80)
            duration = int(_value(item, "duration_ms", 0) or 0)
            changed = list(_value(item, "changed_files", []) or [])
            label = "OK" if ok else ("DENIED" if _value(item, "approval_required", False) else "FAIL")
            detail = _value(item, "output" if ok else "error", "")
            suffix = f" · {duration}ms" if duration else ""
            if changed:
                suffix += f" · {len(changed)} file(s)"
            self._write(f"  [{label}] {tool}{suffix}")
            if (self.verbose or not ok) and detail:
                for line in self._wrap(str(detail).splitlines()[0], indent="    ")[:3]:
                    self._write(line)

    def _render_verification(self, loops: list[Any]) -> None:
        if not loops:
            return
        self._section("Verification")
        for index, verification in enumerate(loops, 1):
            overall = _safe(
                _value(verification, "overall_status", _value(verification, "status", "INCOMPLETE")), 60
            ).upper()
            scope = _safe(_value(verification, "scope", ""), 30).upper()
            stage = _safe(_value(verification, "stage", f"run-{index}"), 40)
            duration = int(_value(verification, "duration_ms", 0) or 0)
            context = " · ".join(part for part in (scope, stage, f"{duration}ms" if duration else "") if part)
            self._write(f"  {overall}" + (f" · {context}" if context else ""))
            summary = _value(verification, "summary", "")
            if summary:
                for line in self._wrap(summary, indent="    ")[:4]:
                    self._write(line)
            for check in list(_value(verification, "checks", []) or []):
                check_meta = _value(check, "check", {})
                result = _value(check, "result", {})
                status = _enum_text(_value(check, "status", "UNKNOWN")).upper()
                name = _safe(_value(check_meta, "name", _value(check, "name", "check")), 160)
                classification = _enum_text(_value(check, "classification", ""))
                check_duration = int(_value(result, "duration_ms", 0) or 0)
                suffix = f" · {check_duration}ms" if check_duration else ""
                if classification and classification not in {"NONE", "None"}:
                    suffix += f" · {classification}"
                self._write(f"    [{status}] {name}{suffix}")
                diagnostic = _value(check, "diagnostic", "") or _value(result, "error", "")
                if diagnostic and status != "PASS":
                    for line in self._wrap(str(diagnostic).splitlines()[0], indent="      ")[:2]:
                        self._write(line)
            for warning in list(_value(verification, "integrity_warnings", []) or [])[:10]:
                self._write(f"    [WARNING] {_safe(warning, 300)}")

    def render_result(self, result: dict[str, Any], *, verification_enabled: bool = True) -> None:
        """Render a final report without inventing missing sections."""
        status = _safe(result.get("final_status", "unknown"), 80).lower()
        runtime = result.get("runtime_task", {}) if isinstance(result.get("runtime_task", {}), dict) else {}
        if self.quiet:
            self._write(status.upper())
            return

        labels = {
            "pass": "COMPLETED · VERIFIED",
            "built": "COMPLETED" if verification_enabled else "COMPLETED · UNVERIFIED",
            "plan": "COMPLETED · PLAN ONLY",
            "verification_failed": "FAILED · VERIFICATION FAIL",
            "verification_incomplete": "BLOCKED · VERIFICATION INCOMPLETE",
            "verification_blocked": "BLOCKED · VERIFICATION BLOCKED",
            "verification_integrity_blocked": "BLOCKED · VERIFICATION INTEGRITY",
            "repair_no_progress": "FAILED · REPAIR NO PROGRESS",
            "blocked": "BLOCKED",
            "aborted": "ABORTED",
            "cancelled": "CANCELLED",
            "configuration_error": "CONFIGURATION ERROR",
            "provider_error": "PROVIDER ERROR",
        }
        self._write()
        self._write(self._styled(labels.get(status, status.upper()), "bold"))

        reply = result.get("chat_reply", "")
        if reply:
            self._section("Summary")
            for line in self._wrap(reply):
                self._write(line)

        changed = list(result.get("changed_files", []) or [])
        rollback = result.get("rollback", {}) if isinstance(result.get("rollback"), dict) else {}
        rolled_back = bool(rollback.get("success"))
        if changed:
            self._section("Changed" + (" (rolled back)" if rolled_back else ""))
            for path in changed[:100]:
                for line in self._wrap(path, indent="  - ", subsequent="    "):
                    self._write(line)

        self._render_tool_results(list(result.get("tool_results", []) or []))
        self._render_verification(list(result.get("verification_loops", []) or []))

        turns = int(runtime.get("model_turn_count", result.get("agent_steps", 0)) or 0)
        tool_calls = int(runtime.get("tool_call_count", result.get("agent_tool_calls", 0)) or 0)
        duration = int(runtime.get("duration_ms", 0) or 0)
        if turns or tool_calls or duration:
            self._section("Runtime")
            bits = []
            if turns:
                bits.append(f"{turns} model turn(s)")
            if tool_calls:
                bits.append(f"{tool_calls} tool call(s)")
            if duration:
                bits.append(f"{duration / 1000:.1f}s")
            self._write("  " + " · ".join(bits))

        transaction_id = result.get("transaction_id") or runtime.get("transaction_id")
        conflicts = list(result.get("transaction_conflicts", []) or [])
        if transaction_id or result.get("undo_available") or rollback or conflicts:
            self._section("Transaction")
            if transaction_id:
                self._write(f"  ID          {_safe(transaction_id, 160)}")
            if rolled_back:
                self._write("  Rollback    COMPLETED")
            elif rollback:
                self._write("  Rollback    CONFLICT OR FAILURE")
            elif result.get("undo_available"):
                self._write("  Rollback    available with /undo")
            for path in conflicts[:30]:
                self._write(f"  [CONFLICT]  {_safe(path, 300)}")

        git_commit = result.get("commit_sha") or result.get("git_commit")
        git_push = result.get("git_push")
        if git_commit or git_push:
            self._section("Git")
            if git_commit:
                self._write(f"  Commit      {_safe(git_commit, 300)}")
            if git_push:
                push_text = "No origin remote configured" if git_push == "__NEEDS_REMOTE__" else _safe(git_push, 300)
                self._write(f"  Publish     {push_text}")


class TerminalRenderer(PlainRenderer):
    """Plain renderer with conservative ANSI styling on capable TTYs."""

    def __init__(self, *, color: bool = True, **kwargs: Any):
        super().__init__(**kwargs)
        is_tty = bool(getattr(self.stream, "isatty", lambda: False)())
        self._color = bool(
            color
            and is_tty
            and "NO_COLOR" not in os.environ
            and os.environ.get("TERM", "").lower() != "dumb"
        )

    @property
    def color_enabled(self) -> bool:
        return self._color

    def _styled(self, text: str, style: str) -> str:
        if not self._color:
            return text
        return f"{ANSI.get(style, '')}{text}{ANSI['reset']}"

    def _write(self, text: str = "", *, error: bool = False) -> None:
        safe = redact_secrets(text)
        if not self._color:
            safe = ANSI_PATTERN.sub("", safe)
        print(safe, file=self.error_stream if error else self.stream)


class JsonRenderer(PlainRenderer):
    """Final JSON on stdout; human progress and approvals stay on stderr."""

    def _read_decision(self) -> str:
        self._write("Decision [D]: ", error=True)
        return self.input_func("")

    def prompt_approval(self, request: CapabilityRequest) -> ApprovalDecision:
        original = self.stream
        self.stream = self.error_stream
        try:
            return super().prompt_approval(request)
        finally:
            self.stream = original

    def render_result(self, result: dict[str, Any], *, verification_enabled: bool = True) -> None:
        from .automation import build_json_result

        json.dump(
            build_json_result(result, verification_enabled=verification_enabled),
            self.stream,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        self.stream.write("\n")
        self.stream.flush()


def create_renderer(
    *,
    stream: TextIO | None = None,
    error_stream: TextIO | None = None,
    plain: bool = False,
    no_color: bool = False,
    quiet: bool = False,
    verbose: bool = False,
    width: int | None = None,
    input_func: Callable[[str], str] | None = None,
    json_output: bool = False,
) -> PlainRenderer:
    if json_output:
        cls = JsonRenderer
    else:
        cls = PlainRenderer if plain else TerminalRenderer
    kwargs = {
        "stream": stream,
        "error_stream": error_stream,
        "quiet": quiet,
        "verbose": verbose,
        "width": width,
        "input_func": input_func,
    }
    if cls is TerminalRenderer:
        return cls(color=not no_color, **kwargs)
    return cls(**kwargs)


__all__ = ["JsonRenderer", "PlainRenderer", "TerminalRenderer", "create_renderer"]
