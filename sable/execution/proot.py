"""Termux-aware PRoot execution backend with truthful guarantee metadata."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Mapping

from ..security import sanitized_environment
from .base import (
    BackendAvailability,
    BackendGuarantees,
    EnforcementLevel,
    EnvironmentPolicy,
    ExecutionBackend,
    ExecutionRequest,
    ExecutionResult,
)
from .native import NativeExecutionBackend


def is_termux_environment(environment: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environment is None else environment
    prefix = str(env.get("PREFIX", ""))
    return bool(env.get("TERMUX_VERSION")) or prefix.startswith("/data/data/com.termux/files/usr")


class ProotExecutionBackend(ExecutionBackend):
    """User-space path remapping for a caller-provided rootfs.

    PRoot is not a kernel security boundary and does not isolate networking,
    processes, privileges, or the kernel filesystem namespace.
    """

    name = "proot"
    guarantees = BackendGuarantees(
        workspace_path_confinement=EnforcementLevel.BEST_EFFORT,
        private_home=EnforcementLevel.ENFORCED,
        sanitized_environment=EnforcementLevel.ENFORCED,
        filesystem_namespace=EnforcementLevel.NOT_SUPPORTED,
        network_isolation=EnforcementLevel.NOT_SUPPORTED,
        process_isolation=EnforcementLevel.NOT_SUPPORTED,
        resource_limits=EnforcementLevel.BEST_EFFORT,
        descendant_cleanup=EnforcementLevel.BEST_EFFORT,
        shell_disabled_by_default=EnforcementLevel.ENFORCED,
    )

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        rootfs: str | Path | None = None,
        proot_path: str | None = None,
        termux: bool | None = None,
        platform: str | None = None,
    ):
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.rootfs = Path(rootfs).expanduser().resolve() if rootfs else None
        self.proot_path = proot_path or shutil.which("proot")
        self.termux = is_termux_environment() if termux is None else bool(termux)
        self.platform = platform or os.name
        self.native = NativeExecutionBackend(self.workspace_root)

    def availability(self) -> BackendAvailability:
        if self.platform != "posix":
            return BackendAvailability(False, "PRoot execution is supported only on POSIX/Termux hosts.")
        if not self.termux:
            return BackendAvailability(False, "Termux was not detected.")
        if not self.proot_path:
            return BackendAvailability(False, "The proot executable was not found on PATH.")
        if self.rootfs is None:
            return BackendAvailability(False, "No PRoot rootfs is configured; Sable never downloads one automatically.")
        if not self.rootfs.is_dir():
            return BackendAvailability(False, f"Configured PRoot rootfs does not exist: {self.rootfs}")
        return BackendAvailability(True, "Termux, proot, and the configured rootfs are available.")

    def build_command(self, request: ExecutionRequest, private_home: Path) -> tuple[list[str], dict[str, str], str]:
        try:
            relative_cwd = request.cwd.resolve().relative_to(self.workspace_root)
        except ValueError as exc:
            raise ValueError("PRoot working directory escapes the configured workspace.") from exc
        inside_cwd = PurePosixPath("/workspace", *relative_cwd.parts).as_posix()
        child_command = ["/bin/sh", "-lc", str(request.argv)] if request.shell else list(request.argv)
        env = sanitized_environment(
            request.env,
            private_home="/home/sable",
            temp_dir="/tmp",
        )
        env["PATH"] = "/usr/local/bin:/usr/bin:/bin"
        command = [
            str(self.proot_path),
            "--kill-on-exit",
            "-0",
            "-r",
            str(self.rootfs),
            "-b",
            f"{self.workspace_root}:/workspace",
            "-b",
            f"{private_home}:/home/sable",
            "-w",
            inside_cwd,
            *child_command,
        ]
        return command, env, inside_cwd

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        availability = self.availability()
        if not availability.available:
            return ExecutionResult(
                self.name,
                False,
                error=f"PRoot backend unavailable: {availability.reason}",
                guarantees=self.guarantees,
                metadata={"backend_available": False},
            )
        with tempfile.TemporaryDirectory(prefix="sable-proot-home-", ignore_cleanup_errors=True) as temp:
            private_home = Path(temp)
            try:
                os.chmod(private_home, 0o700)
            except OSError:
                pass
            try:
                command, env, inside_cwd = self.build_command(request, private_home)
            except (OSError, ValueError) as exc:
                return ExecutionResult(self.name, False, error=str(exc), guarantees=self.guarantees)
            native = self.native.execute(ExecutionRequest(
                argv=command,
                cwd=self.workspace_root,
                timeout_seconds=request.timeout_seconds,
                shell=False,
                env=env,
                environment_policy=EnvironmentPolicy.AMBIENT,
                max_output_chars=request.max_output_chars,
            ))
            metadata = dict(native.metadata)
            metadata.update({
                "backend_available": True,
                "environment_policy": EnvironmentPolicy.PROJECT.value,
                "environment_sanitized": True,
                "private_home": True,
                "filesystem_remapping": EnforcementLevel.BEST_EFFORT.value,
                "inside_cwd": inside_cwd,
                "network_isolation": EnforcementLevel.NOT_SUPPORTED.value,
                "process_isolation": EnforcementLevel.NOT_SUPPORTED.value,
            })
            return ExecutionResult(
                backend=self.name,
                success=native.success,
                output=native.output,
                error=native.error,
                exit_code=native.exit_code,
                duration_ms=native.duration_ms,
                timed_out=native.timed_out,
                terminated=native.terminated,
                truncated=native.truncated,
                guarantees=self.guarantees,
                metadata=metadata,
            )
