"""Hardened portable native subprocess backend.

The backend controls process launch, child environment, default HOME, timeouts,
and best-effort descendant cleanup. It does not isolate the filesystem or
network and is not an OS sandbox.
"""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from ..security import sanitized_environment
from .base import (
    BackendAvailability,
    BackendGuarantees,
    EnforcementLevel,
    EnvironmentPolicy,
    ExecutionBackend,
    ExecutionRequest,
    ExecutionResult,
    bounded_output,
)

try:
    import resource
except ImportError:  # pragma: no cover - unavailable on Windows
    resource = None


class NativeExecutionBackend(ExecutionBackend):
    name = "native"

    def __init__(self, workspace_root: str | Path | None = None):
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None
        self.guarantees = BackendGuarantees(
            workspace_path_confinement=EnforcementLevel.NOT_SUPPORTED,
            private_home=EnforcementLevel.ENFORCED,
            sanitized_environment=EnforcementLevel.ENFORCED,
            filesystem_namespace=EnforcementLevel.NOT_SUPPORTED,
            network_isolation=EnforcementLevel.NOT_SUPPORTED,
            process_isolation=EnforcementLevel.NOT_SUPPORTED,
            resource_limits=EnforcementLevel.BEST_EFFORT if resource is not None else EnforcementLevel.NOT_SUPPORTED,
            descendant_cleanup=EnforcementLevel.BEST_EFFORT,
            shell_disabled_by_default=EnforcementLevel.ENFORCED,
        )

    def availability(self) -> BackendAvailability:
        return BackendAvailability(True, "Python native subprocess execution is available.")

    @staticmethod
    def _resource_limiter(timeout_seconds: int):
        if resource is None:
            return None, []
        supported: list[str] = []
        limits: list[tuple[int, tuple[int, int]]] = []

        def conservative(kind: str, soft: int) -> None:
            constant = getattr(resource, kind, None)
            if constant is None:
                return
            try:
                current_soft, current_hard = resource.getrlimit(constant)
            except (OSError, ValueError):
                return
            desired = soft
            if current_hard != resource.RLIM_INFINITY:
                desired = min(desired, int(current_hard))
            if current_soft != resource.RLIM_INFINITY:
                desired = min(desired, int(current_soft))
            if desired < 1:
                return
            limits.append((constant, (desired, desired)))
            supported.append(kind.removeprefix("RLIMIT_").lower())

        conservative("RLIMIT_CPU", max(30, int(timeout_seconds) + 5))
        conservative("RLIMIT_FSIZE", 512 * 1024 * 1024)
        conservative("RLIMIT_NOFILE", 1024)

        def apply_limits() -> None:
            for kind, value in limits:
                try:
                    resource.setrlimit(kind, value)
                except (OSError, ValueError):
                    continue

        return apply_limits if limits else None, supported

    @staticmethod
    def _prepare_private_environment(request: ExecutionRequest, private_root: Path) -> dict[str, str] | None:
        if request.environment_policy == EnvironmentPolicy.AMBIENT:
            return dict(request.env) if request.env is not None else None

        home = private_root / "home"
        temp = private_root / "tmp"
        for path in (
            home,
            temp,
            home / ".config",
            home / ".cache",
            home / ".local" / "share",
            home / ".local" / "state",
        ):
            path.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(path, 0o700)
            except OSError:
                pass
        return sanitized_environment(request.env, private_home=home, temp_dir=temp)

    @staticmethod
    def _cleanup_process_tree(proc: subprocess.Popen[str]) -> tuple[bool, str]:
        if proc.poll() is not None:
            return True, "process_exited"
        if os.name == "posix":
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=0.75)
                    return True, "process_group_terminated"
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=1.5)
                    return True, "process_group_killed"
            except (OSError, ProcessLookupError, subprocess.SubprocessError):
                try:
                    proc.kill()
                    proc.wait(timeout=1.0)
                    return False, "parent_only_killed"
                except (OSError, subprocess.SubprocessError):
                    return False, "cleanup_failed"

        try:
            proc.send_signal(signal.CTRL_BREAK_EVENT)
            proc.wait(timeout=0.5)
            return False, "windows_group_break"
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                timeout=3,
                shell=False,
                check=False,
            )
            proc.wait(timeout=1.0)
            return False, "windows_taskkill_best_effort"
        except (OSError, subprocess.SubprocessError):
            try:
                proc.kill()
                proc.wait(timeout=1.0)
            except (OSError, subprocess.SubprocessError):
                return False, "cleanup_failed"
            return False, "parent_only_killed"

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        started = time.monotonic()
        command = request.argv if request.shell else list(request.argv)
        limiter, resource_limits = self._resource_limiter(request.timeout_seconds)
        private_context = tempfile.TemporaryDirectory(prefix="sable-exec-", ignore_cleanup_errors=True)
        private_root = Path(private_context.name)
        env = self._prepare_private_environment(request, private_root)
        popen_kwargs: dict[str, object] = {
            "cwd": str(request.cwd),
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "shell": request.shell,
            "env": env,
            "close_fds": True,
        }
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True
            if limiter is not None:
                popen_kwargs["preexec_fn"] = limiter
        elif os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

        metadata: dict[str, object] = {
            "shell": request.shell,
            "environment_policy": request.environment_policy.value,
            "environment_sanitized": request.environment_policy == EnvironmentPolicy.PROJECT,
            "private_home": request.environment_policy == EnvironmentPolicy.PROJECT,
            "process_group": True,
            "resource_limits": resource_limits,
        }
        try:
            proc = subprocess.Popen(command, **popen_kwargs)
            try:
                stdout, stderr = proc.communicate(timeout=int(request.timeout_seconds))
            except subprocess.TimeoutExpired:
                cleaned, cleanup_method = self._cleanup_process_tree(proc)
                metadata["cleanup_method"] = cleanup_method
                metadata["descendant_cleanup_confirmed"] = cleaned
                try:
                    stdout, stderr = proc.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    stdout, stderr = "", ""
                duration = int((time.monotonic() - started) * 1000)
                combined = ((stdout or "") + (stderr or "")).strip()
                message = (combined + f"\nCommand timed out after {int(request.timeout_seconds)}s").strip()
                message, truncated = bounded_output(message, int(request.max_output_chars))
                return ExecutionResult(
                    backend=self.name,
                    success=False,
                    error=message,
                    duration_ms=duration,
                    timed_out=True,
                    terminated=proc.poll() is not None,
                    truncated=truncated,
                    guarantees=self.guarantees,
                    metadata=metadata,
                )

            duration = int((time.monotonic() - started) * 1000)
            combined, truncated = bounded_output(
                ((stdout or "") + (stderr or "")).strip(),
                int(request.max_output_chars),
            )
            success = proc.returncode == 0
            return ExecutionResult(
                backend=self.name,
                success=success,
                output=combined if success else "",
                error="" if success else (combined or f"Exited with code {proc.returncode}"),
                exit_code=proc.returncode,
                duration_ms=duration,
                truncated=truncated,
                guarantees=self.guarantees,
                metadata=metadata,
            )
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            duration = int((time.monotonic() - started) * 1000)
            return ExecutionResult(
                backend=self.name,
                success=False,
                error=str(exc),
                duration_ms=duration,
                guarantees=self.guarantees,
                metadata=metadata,
            )
        finally:
            private_context.cleanup()
