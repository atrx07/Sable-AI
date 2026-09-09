"""Runtime-owned capability classification and scoped human approvals."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

from .config import redact_secrets


class Capability(str, Enum):
    READ_WORKSPACE = "READ_WORKSPACE"
    WRITE_WORKSPACE = "WRITE_WORKSPACE"
    DELETE_WORKSPACE = "DELETE_WORKSPACE"
    EXECUTE_PROCESS = "EXECUTE_PROCESS"
    EXECUTE_SHELL = "EXECUTE_SHELL"
    NETWORK_ACCESS = "NETWORK_ACCESS"
    PACKAGE_INSTALL = "PACKAGE_INSTALL"
    GIT_REMOTE_READ = "GIT_REMOTE_READ"
    GIT_PUBLISH = "GIT_PUBLISH"
    GIT_HISTORY_MUTATION = "GIT_HISTORY_MUTATION"
    EXTERNAL_FILESYSTEM = "EXTERNAL_FILESYSTEM"


class ActionSource(str, Enum):
    MODEL = "MODEL"
    USER = "USER"
    VERIFIER = "VERIFIER"
    RUNTIME = "RUNTIME"


class ApprovalDecision(str, Enum):
    ALLOW_ONCE = "ALLOW_ONCE"
    ALLOW_SESSION = "ALLOW_SESSION"
    DENY = "DENY"


@dataclass(frozen=True)
class CapabilityRequirement:
    capability: Capability
    action: str
    risk: str
    reason: str
    target: str | None = None


@dataclass
class CapabilityRequest:
    request_id: str
    capability: Capability
    action: str
    source: ActionSource
    task_id: str | None
    session_id: str
    tool: str
    risk: str
    reason: str
    target: str | None
    created_at: str
    scope: str
    decision: ApprovalDecision | None = None
    consumed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "capability": self.capability.value,
            "action": self.action,
            "source": self.source.value,
            "task_id": self.task_id,
            "session_id": self.session_id,
            "tool": self.tool,
            "risk": self.risk,
            "reason": self.reason,
            "target": self.target,
            "created_at": self.created_at,
            "scope": self.scope,
            "decision": self.decision.value if self.decision else None,
            "consumed": self.consumed,
        }


@dataclass(frozen=True)
class AuthorizationOutcome:
    allowed: bool
    capability: Capability
    reason: str
    allowed_by: str
    approval_required: bool = False
    request: CapabilityRequest | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "capability": self.capability.value,
            "reason": self.reason,
            "allowed_by": self.allowed_by,
            "approval_required": self.approval_required,
            "request": self.request.to_dict() if self.request else None,
        }


class ApprovalError(RuntimeError):
    pass


ApprovalHandler = Callable[[CapabilityRequest], ApprovalDecision | str]


class ApprovalEngine:
    """In-memory approvals. Session grants intentionally never persist to disk."""

    def __init__(self, *, session_id: str | None = None, handler: ApprovalHandler | None = None):
        self.session_id = session_id or f"volatile-{uuid.uuid4().hex}"
        self.handler = handler
        self._pending: dict[str, CapabilityRequest] = {}
        self._history: list[CapabilityRequest] = []
        self._session_grants: set[tuple[str, Capability, str]] = set()

    def bind_session(self, session_id: str | None) -> None:
        new_id = session_id or f"volatile-{uuid.uuid4().hex}"
        if new_id != self.session_id:
            self._pending.clear()
            self._session_grants.clear()
        self.session_id = new_id

    def set_handler(self, handler: ApprovalHandler | None) -> None:
        self.handler = handler

    def create_request(
        self,
        requirement: CapabilityRequirement,
        *,
        source: ActionSource,
        tool: str,
        task_id: str | None,
        scope_material: Any,
    ) -> CapabilityRequest:
        canonical = json.dumps(scope_material, sort_keys=True, separators=(",", ":"), default=str)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]
        scope = f"{tool}:{digest}"
        request = CapabilityRequest(
            request_id=f"cap-{uuid.uuid4().hex}",
            capability=requirement.capability,
            action=redact_secrets(requirement.action)[:500],
            source=source,
            task_id=str(task_id)[:120] if task_id else None,
            session_id=self.session_id,
            tool=str(tool)[:80],
            risk=str(requirement.risk)[:20],
            reason=redact_secrets(requirement.reason)[:500],
            target=redact_secrets(requirement.target)[:300] if requirement.target else None,
            created_at=datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            scope=scope,
        )
        self._pending[request.request_id] = request
        return request

    def decide(self, request_id: str, decision: ApprovalDecision | str) -> CapabilityRequest:
        request = self._pending.get(str(request_id))
        if request is None or request.decision is not None or request.session_id != self.session_id:
            raise ApprovalError("Approval request is invalid, stale, already consumed, or belongs to another session.")
        try:
            normalized = decision if isinstance(decision, ApprovalDecision) else ApprovalDecision(str(decision).upper())
        except ValueError as exc:
            raise ApprovalError(f"Invalid approval decision: {decision}") from exc
        request.decision = normalized
        request.consumed = normalized == ApprovalDecision.ALLOW_ONCE
        if normalized == ApprovalDecision.ALLOW_SESSION:
            self._session_grants.add((request.session_id, request.capability, request.scope))
        self._pending.pop(request.request_id, None)
        self._history.append(request)
        self._history = self._history[-200:]
        return request

    def authorize(self, request: CapabilityRequest) -> AuthorizationOutcome:
        grant_key = (self.session_id, request.capability, request.scope)
        if request.session_id != self.session_id:
            return AuthorizationOutcome(False, request.capability, "Approval session changed.", "stale", request=request)
        if grant_key in self._session_grants:
            self._pending.pop(request.request_id, None)
            request.decision = ApprovalDecision.ALLOW_SESSION
            self._history.append(request)
            self._history = self._history[-200:]
            return AuthorizationOutcome(True, request.capability, "Matching session-scoped approval reused.", "session", request=request)
        if self.handler is None:
            return AuthorizationOutcome(
                False,
                request.capability,
                "Human approval is required, but no approval handler is available.",
                "pending",
                approval_required=True,
                request=request,
            )
        try:
            decision = self.handler(request)
            decided = self.decide(request.request_id, decision)
        except (ApprovalError, ValueError, TypeError) as exc:
            self._pending.pop(request.request_id, None)
            return AuthorizationOutcome(False, request.capability, redact_secrets(str(exc))[:300], "denied", request=request)
        if decided.decision in {ApprovalDecision.ALLOW_ONCE, ApprovalDecision.ALLOW_SESSION}:
            return AuthorizationOutcome(
                True,
                request.capability,
                "Human approval granted.",
                "once" if decided.decision == ApprovalDecision.ALLOW_ONCE else "session",
                request=decided,
            )
        return AuthorizationOutcome(False, request.capability, "Human approval denied.", "denied", request=decided)

    def pending(self) -> list[dict[str, Any]]:
        return [request.to_dict() for request in self._pending.values()]


class CapabilityPolicy:
    BASELINE = {
        Capability.READ_WORKSPACE,
        Capability.WRITE_WORKSPACE,
        Capability.EXECUTE_PROCESS,
    }
    HARD_DENIED = {Capability.EXTERNAL_FILESYSTEM}

    def evaluate(self, capability: Capability, *, mode: str, source: ActionSource) -> AuthorizationOutcome:
        normalized_mode = mode if mode in {"plan", "build", "yolo"} else "build"
        if capability in self.HARD_DENIED:
            return AuthorizationOutcome(False, capability, "External filesystem access is a hard boundary.", "hard_boundary")
        if normalized_mode == "plan" and capability != Capability.READ_WORKSPACE:
            return AuthorizationOutcome(False, capability, "Plan mode is a hard read-only ceiling.", "plan_ceiling")
        if source == ActionSource.USER:
            return AuthorizationOutcome(True, capability, "Direct user action.", "direct_user")
        if capability in self.BASELINE:
            return AuthorizationOutcome(True, capability, "Allowed by the mode baseline.", "baseline")
        if normalized_mode != "yolo":
            return AuthorizationOutcome(
                False,
                capability,
                f"{capability.value} is not requestable in {normalized_mode} mode; use /mode yolo.",
                "mode_ceiling",
            )
        return AuthorizationOutcome(False, capability, "Human approval is required.", "approval", approval_required=True)


READ_TOOLS = {
    "read_file", "read_file_lines", "list_files", "search_files", "grep_files", "file_info",
    "project_profile", "repo_map", "list_symbols", "find_symbol", "find_references", "read_symbol",
    "find_tests_for_file", "recent_changes", "git_status", "git_diff", "git_log",
}
WRITE_TOOLS = {"write_file", "append_file", "patch_file", "apply_patch", "make_dir", "copy_file", "move_file"}
PACKAGE_MANAGERS = {"pip", "pip3", "npm", "pnpm", "yarn", "bun", "pkg", "apt", "apt-get", "cargo", "go"}
NETWORK_EXECUTABLES = {"curl", "wget", "ssh", "scp", "sftp", "nc", "ncat", "netcat"}
PACKAGE_ACTIONS = {
    "install", "add", "remove", "uninstall", "update", "upgrade", "i", "exec", "dlx",
    "get", "fetch", "download", "publish", "login", "logout", "ci",
}


def _command_requirements(argv: list[str]) -> list[CapabilityRequirement]:
    action = " ".join(str(item) for item in argv)[:500]
    requirements = [CapabilityRequirement(
        Capability.EXECUTE_PROCESS,
        action,
        "normal",
        "Launch a direct project subprocess.",
    )]
    if not argv:
        return requirements
    executable = os.path.basename(argv[0]).lower()
    lowered = [str(item).lower() for item in argv[1:]]
    package_install = executable in PACKAGE_MANAGERS and any(item in PACKAGE_ACTIONS for item in lowered)
    if executable.startswith("python") and "-m" in lowered:
        try:
            package_install = package_install or lowered[lowered.index("-m") + 1] in {"pip", "ensurepip"}
        except IndexError:
            pass
    git_remote = executable == "git" and any(item in {"clone", "fetch", "pull", "push"} for item in lowered)
    if executable in NETWORK_EXECUTABLES or package_install or git_remote:
        requirements.append(CapabilityRequirement(
            Capability.NETWORK_ACCESS,
            action,
            "high",
            "The command is a known direct network-capable action. This policy does not isolate arbitrary process networking.",
        ))
    if package_install:
        requirements.append(CapabilityRequirement(
            Capability.PACKAGE_INSTALL,
            action,
            "high",
            "The command appears to mutate package dependencies or registries.",
        ))
    if executable == "git" and "push" in lowered:
        requirements.append(CapabilityRequirement(Capability.GIT_PUBLISH, action, "high", "The command publishes Git refs."))
    elif git_remote:
        requirements.append(CapabilityRequirement(Capability.GIT_REMOTE_READ, action, "high", "The command accesses a Git remote."))
    return requirements


def requirements_for_tool(tool: str, args: dict[str, Any]) -> list[CapabilityRequirement]:
    """Classify an action without trusting authorization-shaped tool arguments."""

    name = str(tool)
    target = str(args.get("path") or args.get("file") or args.get("branch") or "")[:300] or None
    if name in READ_TOOLS or (name == "git_branch" and not args.get("name")):
        return [CapabilityRequirement(Capability.READ_WORKSPACE, name, "normal", "Read workspace or local Git metadata.", target)]
    if name in WRITE_TOOLS:
        return [CapabilityRequirement(Capability.WRITE_WORKSPACE, name, "normal", "Mutate a workspace path through a transaction-aware file tool.", target)]
    if name == "delete_file":
        return [CapabilityRequirement(Capability.DELETE_WORKSPACE, f"delete {target or '?'}", "high", "Delete a workspace path through the transaction-aware file tool.", target)]
    if name == "run_command":
        return _command_requirements(list(args.get("argv") or []))
    if name == "run_shell":
        command = str(args.get("command") or "")[:500]
        return [CapabilityRequirement(Capability.EXECUTE_SHELL, command, "high", "Execute a raw shell command whose effects cannot be fully classified.")]
    if name in {"git_clone", "git_fetch", "git_pull"}:
        action = f"{name} {target or ''}".strip()
        return [
            CapabilityRequirement(Capability.NETWORK_ACCESS, action, "high", "Access a Git remote."),
            CapabilityRequirement(Capability.GIT_REMOTE_READ, action, "high", "Read or merge data from a Git remote."),
        ]
    if name == "git_push":
        action = f"git push {target or ''}".strip()
        return [
            CapabilityRequirement(Capability.NETWORK_ACCESS, action, "high", "Access a Git remote."),
            CapabilityRequirement(Capability.GIT_PUBLISH, action, "high", "Publish local Git refs to a remote."),
        ]
    if name in {"git_commit", "git_stash", "git_set_remote"} or (name == "git_branch" and args.get("name")):
        return [CapabilityRequirement(Capability.GIT_HISTORY_MUTATION, name, "high", "Mutate local Git state or configuration.", target)]
    return []


def approval_scope_material(tool: str, args: dict[str, Any], capability: Capability) -> dict[str, Any]:
    """Build an exact runtime scope; approval-looking model arguments are ignored."""

    allowed_keys = {
        "delete_file": ("path",),
        "run_shell": ("command", "cwd"),
        "run_command": ("argv", "cwd"),
        "git_push": ("branch",),
        "git_pull": ("branch",),
        "git_clone": ("url", "dest"),
        "git_branch": ("name",),
        "git_commit": ("message",),
    }.get(tool, tuple())
    return {
        "tool": tool,
        "capability": capability.value,
        "args": {key: args.get(key) for key in allowed_keys},
    }
