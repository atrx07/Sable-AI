"""Workspace confinement and permission policy for Sable tools."""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Any

VALID_MODES = {"plan", "build", "yolo"}

READ_ONLY_TOOLS = {
    "read_file",
    "read_file_lines",
    "list_files",
    "search_files",
    "grep_files",
    "file_info",
    "project_profile",
    "git_status",
    "git_log",
    "git_diff",
}

WRITE_TOOLS = {
    "write_file",
    "append_file",
    "patch_file",
    "make_dir",
    "copy_file",
    "move_file",
}

HIGH_RISK_TOOLS = {
    "delete_file",
    "run_shell",
    "git_commit",
    "git_push",
    "git_pull",
    "git_clone",
    "git_stash",
    "git_set_remote",
}

DANGEROUS_EXECUTABLES = {
    "curl", "wget", "ssh", "scp", "sftp", "nc", "ncat", "netcat",
    "sudo", "su", "mount", "umount", "dd", "mkfs", "fdisk",
}

PACKAGE_MANAGERS = {"pip", "pip3", "npm", "pnpm", "yarn", "bun", "pkg", "apt", "apt-get"}

SAFE_BUILD_EXECUTABLES = {
    "python", "python3", "pytest", "ruff", "mypy",
    "node", "npm", "pnpm", "yarn", "bun",
    "cargo", "rustc", "go", "java", "javac",
    "gcc", "g++", "clang", "clang++", "make", "cmake", "ctest",
}


class WorkspaceViolation(PermissionError):
    pass


class Workspace:
    """Canonical path resolver that cannot escape a fixed project root."""

    def __init__(self, root: str):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.cwd = self.root

    def resolve(self, path: str | os.PathLike[str] = ".", *, base: Path | None = None) -> Path:
        raw = Path(path).expanduser()
        if raw.is_absolute():
            target = raw.resolve(strict=False)
        else:
            target = ((base or self.cwd) / raw).resolve(strict=False)
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceViolation(f"Path escapes workspace: {path}") from exc
        return target

    def relative(self, path: Path) -> str:
        return str(path.resolve(strict=False).relative_to(self.root)) or "."

    def chdir(self, path: str) -> Path:
        target = self.resolve(path)
        if not target.exists():
            target.mkdir(parents=True, exist_ok=True)
        if not target.is_dir():
            raise NotADirectoryError(path)
        self.cwd = target
        return target


class PermissionPolicy:
    """Hard permission boundary independent of model instructions."""

    def __init__(self, mode: str = "build"):
        self.mode = mode if mode in VALID_MODES else "build"

    def check(self, tool: str, args: dict[str, Any]) -> tuple[bool, str]:
        if self.mode == "plan" and tool not in READ_ONLY_TOOLS:
            return False, f"'{tool}' is disabled in plan mode. Switch to /mode build or /mode yolo."
        if self.mode == "plan" and tool == "git_branch" and args.get("name"):
            return False, "Creating or switching branches is disabled in plan mode."

        if self.mode != "yolo" and tool in HIGH_RISK_TOOLS:
            return False, (
                f"'{tool}' requires explicit high-risk permission. "
                "Use /mode yolo for this request, or run the matching slash command manually."
            )

        if tool == "run_command":
            return self._check_command(args)

        return True, ""

    def _check_command(self, args: dict[str, Any]) -> tuple[bool, str]:
        argv = args.get("argv") or []
        if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
            return False, "run_command requires a non-empty argv string array."

        exe = os.path.basename(argv[0]).lower()
        lowered = [x.lower() for x in argv[1:]]

        if self.mode != "yolo" and exe not in SAFE_BUILD_EXECUTABLES:
            return False, (
                f"Generic executable '{exe}' is not allow-listed in build mode. "
                "Use a dedicated Sable tool or /mode yolo."
            )

        # Do not let argv smuggle a direct outside-workspace path around tool confinement.
        if self.mode != "yolo":
            for token in argv[1:]:
                normalized = token.replace("\\", "/")
                if normalized.startswith("/") or normalized == ".." or normalized.startswith("../") or "/../" in normalized:
                    return False, "Absolute or parent-traversal command arguments require /mode yolo."

        if exe == "git" and self.mode != "yolo":
            return False, "Git commands must use Sable's dedicated Git tools outside /mode yolo."

        if exe in DANGEROUS_EXECUTABLES and self.mode != "yolo":
            return False, f"Network/system command '{exe}' requires /mode yolo."

        if exe in PACKAGE_MANAGERS and self.mode != "yolo":
            # Running package-manager scripts is fine; installing/mutating packages is not.
            mutating = {"install", "add", "remove", "uninstall", "update", "upgrade", "i"}
            if any(token in mutating for token in lowered[:2]):
                return False, f"Package changes through '{exe}' require /mode yolo."

        if exe.startswith("python"):
            # `python -c` can trivially bypass the workspace jail.
            if "-c" in lowered and self.mode != "yolo":
                return False, "python -c is blocked outside /mode yolo."
            if "-m" in lowered:
                try:
                    module = lowered[lowered.index("-m") + 1]
                except (ValueError, IndexError):
                    module = ""
                if module in {"pip", "ensurepip"} and self.mode != "yolo":
                    return False, "Python package installation requires /mode yolo."

        if exe == "node" and self.mode != "yolo" and any(flag in lowered for flag in ("-e", "--eval", "-p", "--print")):
            return False, "node eval/print execution is blocked outside /mode yolo."

        # Shell metacharacters should never be needed because subprocess uses shell=False.
        if any(any(c in token for c in (";", "&&", "||", "`", "$(")) for token in argv):
            return False, "Shell syntax is not accepted by run_command; use argv items or /mode yolo + run_shell."

        return True, ""


def split_user_command(command: str) -> list[str]:
    return shlex.split(command, posix=True)
