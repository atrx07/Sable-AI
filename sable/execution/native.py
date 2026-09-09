"""Portable native subprocess backend.

This backend is a compatibility execution layer, not an OS sandbox. M4.3 adds
stronger process hygiene while retaining the truthful guarantee declarations
defined here.
"""

from __future__ import annotations

import subprocess
import time

from .base import (
    BackendAvailability,
    BackendGuarantees,
    EnforcementLevel,
    ExecutionBackend,
    ExecutionRequest,
    ExecutionResult,
    bounded_output,
)


class NativeExecutionBackend(ExecutionBackend):
    name = "native"
    guarantees = BackendGuarantees(
        workspace_path_confinement=EnforcementLevel.NOT_SUPPORTED,
        private_home=EnforcementLevel.NOT_SUPPORTED,
        sanitized_environment=EnforcementLevel.BEST_EFFORT,
        filesystem_namespace=EnforcementLevel.NOT_SUPPORTED,
        network_isolation=EnforcementLevel.NOT_SUPPORTED,
        process_isolation=EnforcementLevel.NOT_SUPPORTED,
        resource_limits=EnforcementLevel.NOT_SUPPORTED,
        descendant_cleanup=EnforcementLevel.NOT_SUPPORTED,
        shell_disabled_by_default=EnforcementLevel.ENFORCED,
    )

    def availability(self) -> BackendAvailability:
        return BackendAvailability(True, "Python native subprocess execution is available.")

    @staticmethod
    def _timeout_stream(value: str | bytes | None) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return value or ""

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        started = time.monotonic()
        command = request.argv if request.shell else list(request.argv)
        try:
            proc = subprocess.run(
                command,
                cwd=str(request.cwd),
                capture_output=True,
                text=True,
                timeout=int(request.timeout_seconds),
                shell=request.shell,
                env=dict(request.env) if request.env is not None else None,
                close_fds=True,
            )
            duration = int((time.monotonic() - started) * 1000)
            combined, truncated = bounded_output(
                ((proc.stdout or "") + (proc.stderr or "")).strip(),
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
                metadata={"shell": request.shell, "environment_supplied": request.env is not None},
            )
        except subprocess.TimeoutExpired as exc:
            duration = int((time.monotonic() - started) * 1000)
            captured = (self._timeout_stream(exc.stdout) + self._timeout_stream(exc.stderr)).strip()
            message = (captured + f"\nCommand timed out after {int(request.timeout_seconds)}s").strip()
            message, truncated = bounded_output(message, int(request.max_output_chars))
            return ExecutionResult(
                backend=self.name,
                success=False,
                error=message,
                duration_ms=duration,
                timed_out=True,
                truncated=truncated,
                guarantees=self.guarantees,
                metadata={"shell": request.shell, "environment_supplied": request.env is not None},
            )
        except (OSError, ValueError) as exc:
            duration = int((time.monotonic() - started) * 1000)
            return ExecutionResult(
                backend=self.name,
                success=False,
                error=str(exc),
                duration_ms=duration,
                guarantees=self.guarantees,
                metadata={"shell": request.shell, "environment_supplied": request.env is not None},
            )
