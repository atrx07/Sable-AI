"""Offline, read-only diagnostics for Sable and a selected workspace."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .cli_args import ExitCode
from .config import CONFIG_DIR, CONFIG_FILE, DEFAULTS, get_active_key, is_blocked_path, redact_secrets
from .execution import NativeExecutionBackend, ProotExecutionBackend, is_termux_environment
from .verification import VerificationDiscovery, VerificationScope


@dataclass(frozen=True)
class DiagnosticCheck:
    section: str
    name: str
    status: str
    detail: str
    critical: bool = False
    exit_code: int = int(ExitCode.SUCCESS)

    def to_dict(self) -> dict[str, Any]:
        return {
            "section": self.section,
            "name": self.name,
            "status": self.status,
            "detail": redact_secrets(self.detail)[:1000],
            "critical": self.critical,
        }


@dataclass
class DoctorReport:
    workspace: str
    checks: list[DiagnosticCheck] = field(default_factory=list)
    offline: bool = True

    @property
    def exit_code(self) -> ExitCode:
        failures = [item for item in self.checks if item.critical and item.status == "FAIL"]
        if not failures:
            return ExitCode.SUCCESS
        if any(item.exit_code == int(ExitCode.BACKEND_UNAVAILABLE) for item in failures):
            return ExitCode.BACKEND_UNAVAILABLE
        return ExitCode.USAGE

    def counts(self) -> dict[str, int]:
        return {
            status: sum(1 for item in self.checks if item.status == status)
            for status in ("PASS", "WARN", "INFO", "UNAVAILABLE", "FAIL")
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "offline": self.offline,
            "workspace": self.workspace,
            "status": "ready" if self.exit_code == ExitCode.SUCCESS else "not_ready",
            "exit_code": int(self.exit_code),
            "counts": self.counts(),
            "checks": [item.to_dict() for item in self.checks],
        }


def _nearest_existing(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def _load_config_read_only(path: Path) -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    cfg["token_usage"] = dict(DEFAULTS["token_usage"])
    cfg["rate_limits"] = {}
    saved: dict[str, Any] = {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        if isinstance(loaded, dict):
            saved = loaded
            cfg.update(saved)
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass
    if "debug_model" in saved and "fast_model" not in saved:
        cfg["fast_model"] = saved.get("debug_model") or DEFAULTS["fast_model"]
    cfg.pop("debug_model", None)
    env_key = os.environ.get("GROQ_API_KEY", "")
    if env_key and not cfg.get("groq_key_1"):
        cfg["groq_key_1"] = env_key
    return cfg


def _git(
    workspace: Path,
    args: list[str],
    *,
    runner: Callable[..., Any],
) -> tuple[bool, str]:
    try:
        completed = runner(
            ["git", "-C", str(workspace), *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False, ""
    output = (completed.stdout or "").strip()
    return completed.returncode == 0, redact_secrets(output)[:1000]


def diagnose(
    workspace: str | Path,
    *,
    config: dict[str, Any] | None = None,
    config_dir: str | Path | None = None,
    config_file: str | Path | None = None,
    access: Callable[[Any, int], bool] = os.access,
    which: Callable[[str], str | None] = shutil.which,
    runner: Callable[..., Any] = subprocess.run,
) -> DoctorReport:
    """Inspect known local state without network calls, installs, or writes."""
    root = Path(workspace).expanduser().resolve()
    cfg = dict(_load_config_read_only(Path(config_file or CONFIG_FILE)) if config is None else config)
    control = Path(config_dir or CONFIG_DIR).expanduser()
    report = DoctorReport(str(root))

    py_minimum = sys.version_info[:2] >= (3, 10)
    py_ci_tested = sys.version_info[:2] <= (3, 13)
    report.checks.append(DiagnosticCheck(
        "Sable", "version", "PASS", f"Sable {__version__}",
    ))
    report.checks.append(DiagnosticCheck(
        "Sable", "python", "PASS" if py_ci_tested else ("WARN" if py_minimum else "FAIL"),
        f"Python {sys.version.split()[0]} (CI-tested: 3.10-3.13)", critical=not py_minimum,
    ))
    control_parent = _nearest_existing(control)
    control_ok = access(control_parent, os.R_OK | os.W_OK)
    report.checks.append(DiagnosticCheck(
        "Sable", "config storage", "PASS" if control_ok else "FAIL",
        "accessible" if control_ok else "config storage is not writable",
        critical=not control_ok,
    ))

    if is_blocked_path(str(root)):
        report.checks.append(DiagnosticCheck(
            "Workspace", "protected workspace", "FAIL",
            "refused before repository or verification discovery", critical=True,
        ))
        return report

    readable = root.is_dir() and access(root, os.R_OK)
    writable = root.is_dir() and access(root, os.W_OK)
    report.checks.append(DiagnosticCheck(
        "Workspace", "read access", "PASS" if readable else "FAIL",
        str(root), critical=not readable,
    ))
    build_expected = str(cfg.get("mode", "build")).lower() != "plan"
    report.checks.append(DiagnosticCheck(
        "Workspace", "write access", "PASS" if writable else ("FAIL" if build_expected else "WARN"),
        "writable" if writable else "not writable",
        critical=bool(build_expected and not writable),
    ))
    policy_ok = is_blocked_path(".env") and is_blocked_path(".sable/config.json")
    report.checks.append(DiagnosticCheck(
        "Workspace", "protected paths", "PASS" if policy_ok else "FAIL",
        "protected-path policy active" if policy_ok else "protected-path policy unavailable",
        critical=not policy_ok,
    ))

    try:
        key, _ = get_active_key(cfg)
    except (TypeError, ValueError):
        key = ""
        report.checks.append(DiagnosticCheck(
            "Provider", "configuration", "FAIL", "invalid active key selection", critical=True,
        ))
    report.checks.extend([
        DiagnosticCheck("Provider", "provider", "PASS", "Groq (offline check only)"),
        DiagnosticCheck("Provider", "main model", "PASS" if cfg.get("main_model") else "FAIL", _model_detail(cfg.get("main_model")), critical=not bool(cfg.get("main_model"))),
        DiagnosticCheck("Provider", "fast model", "PASS" if cfg.get("fast_model") else "FAIL", _model_detail(cfg.get("fast_model")), critical=not bool(cfg.get("fast_model"))),
        DiagnosticCheck("Provider", "API key", "PASS" if key else "FAIL", "configured" if key else "not configured", critical=not bool(key)),
    ])

    git_path = which("git")
    if not git_path:
        report.checks.append(DiagnosticCheck("Git", "executable", "UNAVAILABLE", "git not found on PATH"))
    else:
        report.checks.append(DiagnosticCheck("Git", "executable", "PASS", "available"))
        in_repo, _ = _git(root, ["rev-parse", "--show-toplevel"], runner=runner)
        if not in_repo:
            report.checks.append(DiagnosticCheck("Git", "repository", "INFO", "non-Git workspace"))
        else:
            _, branch = _git(root, ["branch", "--show-current"], runner=runner)
            _, dirty = _git(root, ["status", "--porcelain"], runner=runner)
            remote, _ = _git(root, ["remote", "get-url", "origin"], runner=runner)
            report.checks.extend([
                DiagnosticCheck("Git", "repository", "PASS", f"repository on {branch or 'detached HEAD'}"),
                DiagnosticCheck("Git", "working tree", "WARN" if dirty else "PASS", "dirty" if dirty else "clean"),
                DiagnosticCheck("Git", "origin", "PASS" if remote else "INFO", "configured" if remote else "not configured"),
            ])

    requested_backend = str(cfg.get("execution_backend", "auto") or "auto").lower()
    proot = ProotExecutionBackend(root, rootfs=cfg.get("proot_rootfs") or None)
    native = NativeExecutionBackend(root)
    proot_availability = proot.availability()
    if requested_backend not in {"auto", "native", "proot"}:
        report.checks.append(DiagnosticCheck(
            "Execution", "backend", "FAIL",
            f"unknown configured backend: {redact_secrets(requested_backend)[:80]}",
            critical=True, exit_code=int(ExitCode.BACKEND_UNAVAILABLE),
        ))
        selected = native
        availability = native.availability()
    elif requested_backend == "proot":
        selected = proot
        availability = proot_availability
    else:
        selected = native if requested_backend == "native" or not proot_availability.available else proot
        availability = selected.availability()
    backend_critical = not availability.available
    if requested_backend in {"auto", "native", "proot"}:
        report.checks.append(DiagnosticCheck(
            "Execution", "backend", "PASS" if availability.available else "FAIL",
            f"{selected.name}: {availability.reason}", critical=backend_critical,
            exit_code=int(ExitCode.BACKEND_UNAVAILABLE),
        ))
    report.checks.extend([
        DiagnosticCheck("Execution", "Termux", "PASS" if is_termux_environment() else "INFO", "detected" if is_termux_environment() else "not detected"),
        DiagnosticCheck(
            "Execution", "PRoot", "PASS" if proot_availability.available else "UNAVAILABLE",
            proot_availability.reason or "unavailable",
            critical=bool(requested_backend == "proot" and not proot_availability.available),
            exit_code=int(ExitCode.BACKEND_UNAVAILABLE),
        ),
    ])
    guarantees = selected.guarantees.to_dict()
    missing_isolation = [
        name for name in ("filesystem_namespace", "network_isolation", "process_isolation")
        if guarantees.get(name) == "NOT_SUPPORTED"
    ]
    report.checks.append(DiagnosticCheck(
        "Execution", "isolation", "WARN" if missing_isolation else "PASS",
        "not supported: " + ", ".join(missing_isolation) if missing_isolation else "configured guarantees available",
    ))

    verify_enabled = bool(cfg.get("verify_after_changes", True))
    try:
        scope = VerificationScope.parse(cfg.get("verification_scope", "affected"))
    except ValueError:
        scope = VerificationScope.AFFECTED
        report.checks.append(DiagnosticCheck("Verification", "scope", "WARN", "invalid configured scope; affected fallback"))
    else:
        report.checks.append(DiagnosticCheck(
            "Verification", "configuration", "PASS" if verify_enabled else "WARN",
            f"{'enabled' if verify_enabled else 'disabled'} | {scope.value.lower()}",
        ))
    try:
        discovery = VerificationDiscovery(root, executable_finder=which).discover([], scope=scope)
        adapters = ", ".join(discovery.adapters) or "none detected"
        report.checks.append(DiagnosticCheck("Verification", "toolchains", "PASS" if discovery.adapters else "INFO", adapters))
        seen: set[tuple[str, str]] = set()
        for check in discovery.checks:
            key_name = (check.name, check.availability.value)
            if key_name in seen:
                continue
            seen.add(key_name)
            available = check.availability.value in {"AVAILABLE", "CONFIGURED"}
            report.checks.append(DiagnosticCheck(
                "Verification", check.name, "PASS" if available else "UNAVAILABLE",
                check.availability.value.lower().replace("_", " "),
            ))
            if len(seen) >= 20:
                break
        for warning in discovery.warnings[:10]:
            report.checks.append(DiagnosticCheck("Verification", "discovery", "WARN", redact_secrets(warning)[:500]))
    except (OSError, ValueError) as exc:
        report.checks.append(DiagnosticCheck("Verification", "discovery", "WARN", redact_secrets(str(exc))[:500]))

    fallback_ok = access(Path(tempfile.gettempdir()), os.R_OK | os.W_OK)
    storage_ok = control_ok or fallback_ok
    for name in ("session storage", "trace persistence", "transaction storage"):
        report.checks.append(DiagnosticCheck(
            "Runtime", name, "PASS" if storage_ok else "FAIL",
            "primary storage accessible" if control_ok else ("temporary fallback accessible" if fallback_ok else "no writable storage"),
            critical=not storage_ok,
        ))
    return report


def _model_detail(value: Any) -> str:
    return redact_secrets(str(value or "not configured"))[:300]


def render_text(report: DoctorReport) -> str:
    lines = ["Sable Doctor", f"Workspace: {report.workspace}", "Mode: offline", ""]
    section = None
    for check in report.checks:
        if check.section != section:
            section = check.section
            lines.append(section.upper())
        lines.append(f"  [{check.status}] {check.name}: {redact_secrets(check.detail)}")
    counts = report.counts()
    lines.extend([
        "",
        "Summary: " + " | ".join(
            f"{counts[name]} {name.lower()}" for name in ("PASS", "WARN", "INFO", "UNAVAILABLE", "FAIL") if counts[name]
        ),
        f"Result: {'READY' if report.exit_code == ExitCode.SUCCESS else 'NOT READY'}",
    ])
    return "\n".join(lines)


__all__ = ["DiagnosticCheck", "DoctorReport", "diagnose", "render_text"]
