"""Workspace confinement, command environment hardening, and permission policy."""

from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .config import contains_secret

VALID_MODES = {"plan", "build", "yolo"}

READ_ONLY_TOOLS = {
    "read_file",
    "read_file_lines",
    "list_files",
    "search_files",
    "grep_files",
    "file_info",
    "project_profile",
    "repo_map",
    "list_symbols",
    "find_symbol",
    "find_references",
    "read_symbol",
    "find_tests_for_file",
    "recent_changes",
    "git_status",
    "git_log",
    "git_diff",
    "git_branch",
}

WRITE_TOOLS = {
    "write_file",
    "append_file",
    "patch_file",
    "apply_patch",
    "make_dir",
    "copy_file",
    "move_file",
}

DANGEROUS_EXECUTABLES = {
    "curl",
    "wget",
    "ssh",
    "scp",
    "sftp",
    "nc",
    "ncat",
    "netcat",
    "sudo",
    "su",
    "mount",
    "umount",
    "dd",
    "mkfs",
    "fdisk",
}

PACKAGE_MANAGERS = {
    "pip",
    "pip3",
    "npm",
    "pnpm",
    "yarn",
    "bun",
    "pkg",
    "apt",
    "apt-get",
    "cargo",
    "go",
}

SAFE_BUILD_EXECUTABLES = {
    "python",
    "python3",
    "pytest",
    "ruff",
    "flake8",
    "black",
    "mypy",
    "pyright",
    "node",
    "npm",
    "pnpm",
    "yarn",
    "bun",
    "tsc",
    "eslint",
    "prettier",
    "vitest",
    "jest",
    "cargo",
    "rustc",
    "go",
    "gofmt",
    "java",
    "javac",
    "mvn",
    "mvn.cmd",
    "mvnw",
    "mvnw.cmd",
    "gradle",
    "gradlew",
    "gradlew.bat",
    "gcc",
    "g++",
    "clang",
    "clang++",
    "make",
    "cmake",
    "ctest",
}

# These are deliberately conservative patterns for inherited process credentials.
# They protect common API/session/auth variables without pretending to be a full OS sandbox.
SENSITIVE_ENV_EXACT = {
    "SSH_AUTH_SOCK",
    "SSH_AGENT_PID",
    "GPG_AGENT_INFO",
    "GIT_ASKPASS",
    "SSH_ASKPASS",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "KUBECONFIG",
    "DOCKER_CONFIG",
    "NPM_CONFIG_USERCONFIG",
    "PIP_CONFIG_FILE",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM",
    "AWS_CONFIG_FILE",
    "AWS_SHARED_CREDENTIALS_FILE",
    "AZURE_CONFIG_DIR",
    "CLOUDSDK_CONFIG",
    "NETRC",
    "PGPASSFILE",
}
SENSITIVE_ENV_PARTS = (
    "_TOKEN",
    "TOKEN_",
    "_SECRET",
    "SECRET_",
    "_PASSWORD",
    "PASSWORD_",
    "_PASSWD",
    "PASSWD_",
    "_API_KEY",
    "API_KEY_",
    "APIKEY",
    "_CREDENTIAL",
    "CREDENTIAL_",
    "_PRIVATE_KEY",
    "PRIVATE_KEY_",
    "_COOKIE",
    "COOKIE_",
    "AUTH_TOKEN",
    "ACCESS_TOKEN",
    "_AUTH",
    "AUTH_",
    "_KEY",
    "KEY_",
)


class WorkspaceViolation(PermissionError):
    pass


def _looks_sensitive_env_name(name: str) -> bool:
    upper = str(name).upper()
    if upper.startswith("GIT_CONFIG_"):
        # Git's count/key/value triplets must be removed as one family. Keeping
        # only COUNT or VALUE entries both leaks injected config and makes child
        # Git processes fail with a malformed command-line configuration.
        return True
    if upper in SENSITIVE_ENV_EXACT:
        return True
    return any(part in upper for part in SENSITIVE_ENV_PARTS)


# Vars Git authentication needs; everything else sensitive is stripped for Git.
GIT_AUTH_ENV_ALLOWLIST = frozenset(
    {
        "SSH_AUTH_SOCK",
        "SSH_AGENT_PID",
        "GIT_SSH",
        "GIT_SSH_COMMAND",
    }
)


# Config overrides applied to every Sable-owned git subprocess. A `-c` flag
# outranks system/global/local config and include.path indirection, so a
# repository cannot re-enable what these disable. hooksPath=/dev/null means
# "no hooks directory exists"; fsmonitor=false disables repository
# filesystem monitors — without it, a repo-configured monitor script runs
# on routine index-refreshing reads (status/diff/ls-files) that the hook
# suppression does not cover. Every Sable-owned git argv must go through
# safe_git_argv(); a bare ["git", ...] call is a bug.
GIT_SAFE_CONFIG: tuple[str, ...] = (
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fsmonitor=false",
)


def safe_git_argv(args: list[str]) -> list[str]:
    """Prepend the safety config overrides to a git argv (no mutation)."""
    return ["git", *GIT_SAFE_CONFIG, *args]


def git_environment(
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Ambient environment for Git subprocesses: secrets stripped, Git auth kept.

    Starts from the ambient env, removes anything :func:`_looks_sensitive_env_name`
    flags except the Git-auth allowlist above, drops any remaining variable
    whose *value* matches a secret pattern (a name-blind key such as
    ``DATABASE_URL`` or ``NORMAL_SETTING`` must not smuggle a token past the
    name check), and forces ``GIT_TERMINAL_PROMPT=0`` so a remote needing
    credentials fails fast instead of prompting. The allowlisted Git-auth
    vars are exempt from the value check: their values are socket paths and
    ssh commands, not secrets. This is defense in depth beside disabling
    repo hooks: a hook (or any repo-driven helper) that somehow runs must
    not find provider keys in its environment.
    The allowlist is deliberately narrow — SSH-agent auth and an explicit
    ``GIT_SSH``/``GIT_SSH_COMMAND`` only. ``GIT_ASKPASS``/``SSH_ASKPASS`` and
    ``GIT_CONFIG_*`` stay stripped, as in :func:`sanitized_environment`.
    """
    source = dict(os.environ if base is None else base)
    clean = {
        str(key): str(value)
        for key, value in source.items()
        if str(key).upper() in GIT_AUTH_ENV_ALLOWLIST
        or (not _looks_sensitive_env_name(str(key)) and not contains_secret(value))
    }
    clean["GIT_TERMINAL_PROMPT"] = "0"
    return clean


# Git filter-driver policy (issue #23). A repository can configure
# ``filter.<driver>.clean`` / ``.smudge`` / ``.process`` commands that Git
# executes whenever it hashes worktree files — which is what routine
# inspection (status/diff/ls-files) does. Unlike fsmonitor, Git has no
# ``-c`` off-switch for filters, so Sable scans the effective config
# (``git config --list``, all scopes, include.path indirection included)
# before every Sable-owned Git operation and applies the active policy.
# The scan lists config only; listing never executes a filter driver.
FILTER_POLICIES: tuple[str, ...] = ("strict", "permissive")
DEFAULT_FILTER_POLICY = "strict"
FILTER_POLICY_ENV_VAR = "SABLE_GIT_FILTER_POLICY"
FILTER_POLICY_CONFIG_KEY = "git_filter_policy"

# Exact git-lfs driver commands, as written by `git lfs install`.
# Strict mode allowlists exactly these token sequences; anything else is
# refused. `filter-process` is the third filter-driver key git-lfs uses
# (alongside clean/smudge) and is allowlisted in the same way.
_LFS_FILTER_COMMANDS: frozenset[tuple[str, ...]] = frozenset(
    {
        ("git-lfs", "clean", "--", "%f"),
        ("git-lfs", "smudge", "--", "%f"),
        ("git-lfs", "filter-process"),
        ("git-lfs", "clean"),
        ("git-lfs", "smudge"),
    }
)

# Subcommands that never hash worktree files (and therefore never run a
# clean/smudge filter). Used only for the permissive read-only carve-out;
# anything unlisted is treated as mutating (fail closed). `branch` and
# `remote` are read-only only in their flag-only / get-url forms — a
# branch name switches or creates, and remote add/set-url mutates config.
_READ_ONLY_GIT_SUBCOMMANDS = {
    "status",
    "diff",
    "log",
    "ls-files",
    "rev-parse",
    "rev-list",
    "show",
    "config",
    "check-ref-format",
    "grep",
    "ls-remote",
}


def resolve_filter_policy(value: Any = None, *, config: Mapping[str, Any] | None = None) -> str:
    """Resolve the active git filter policy.

    Precedence: explicit value, then the ``git_filter_policy`` config key,
    then ``SABLE_GIT_FILTER_POLICY``, then ``"strict"``. An unknown value
    is rejected loudly instead of silently falling back — a misconfigured
    security policy must fail closed with a clear error, not quietly
    downgrade to a different posture.
    """
    raw: Any = value
    if raw is None and config is not None:
        raw = config.get(FILTER_POLICY_CONFIG_KEY)
    if raw is None:
        raw = os.environ.get(FILTER_POLICY_ENV_VAR)
    if raw is None:
        return DEFAULT_FILTER_POLICY
    normalized = str(raw).strip().lower()
    if normalized not in FILTER_POLICIES:
        raise ValueError(
            f"Invalid git filter policy {raw!r}: expected one of "
            f"{', '.join(repr(p) for p in FILTER_POLICIES)}. "
            f"Set {FILTER_POLICY_CONFIG_KEY} in ~/.sable/config.json "
            f"or {FILTER_POLICY_ENV_VAR} to a valid value."
        )
    return normalized


def _filter_command_tokens(value: str) -> list[str]:
    """Split a filter-driver command into tokens.

    Unparseable quotes fall back to the raw first word.
    """
    try:
        return shlex.split(value)
    except ValueError:
        return value.strip().split(None, 1)[:1]


def _is_lfs_filter_command(value: str, *, exact: bool) -> bool:
    """True when a filter-driver command is a git-lfs driver.

    Strict mode requires an exact token match against the canonical
    ``git lfs install`` shapes. Permissive mode accepts any command whose
    first token is ``git-lfs`` (a prefix match).
    """
    tokens = _filter_command_tokens(value)
    if not tokens:
        return False
    if tokens[0] != "git-lfs":
        return False
    if not exact:
        return True
    return tuple(tokens) in _LFS_FILTER_COMMANDS


def is_read_only_git_args(args: list[str]) -> bool:
    """True when a git argv only reads (never hashes worktree files for a filter).

    Used for the permissive read-only carve-out. Unknown subcommands are
    mutating (fail closed).
    """
    if not args:
        return False
    sub = args[0]
    if sub == "branch":
        # Flag-only forms list/show; a positional name switches or creates.
        return all(item.startswith("-") for item in args[1:])
    if sub == "remote":
        return len(args) > 1 and args[1] == "get-url"
    return sub in _READ_ONLY_GIT_SUBCOMMANDS


@dataclass(frozen=True)
class FilterPolicyCheck:
    """Outcome of scanning the effective git config for filter drivers."""

    allowed: bool
    refused_driver: str | None = None
    refused_command: str | None = None
    warning: str | None = None
    message: str = ""


def _opt_in_hint() -> str:
    return (
        f"opt into the forgiving mode with {FILTER_POLICY_CONFIG_KEY}='permissive' "
        f"in ~/.sable/config.json (or {FILTER_POLICY_ENV_VAR}=permissive)"
    )


def _refusal_message(driver: str, command: str, policy: str, *, read_only: bool) -> str:
    if policy == "permissive" and not read_only:
        return (
            f"Refused: repository configures Git filter driver '{driver}' "
            f"(filter.{driver} runs '{command}') which can execute code. "
            f"Sable's git filter policy is 'permissive': the read-only carve-out "
            f"covers inspection only, so this mutating operation is refused. "
            f"Remove the filter, or run this operation outside Sable."
        )
    return (
        f"Refused: repository configures Git filter driver '{driver}' "
        f"(filter.{driver} runs '{command}') which can execute code on routine "
        f"Git operations. Sable's git filter policy is 'strict': only exact "
        f"git-lfs drivers are allowed. Remove the filter, or "
        f"{_opt_in_hint()}."
    )


def _warning_message(driver: str, command: str) -> str:
    return (
        f"Warning: repository configures Git filter driver '{driver}' "
        f"(filter.{driver} runs '{command}') which can execute code; running it "
        f"under git filter policy 'permissive' (read-only carve-out). "
        f"Set {FILTER_POLICY_CONFIG_KEY}='strict' in ~/.sable/config.json "
        f"to refuse non-LFS filters."
    )


def check_git_filter_policy(
    cwd: str | Path,
    *,
    policy: str | None = None,
    read_only: bool = False,
    config: Mapping[str, Any] | None = None,
) -> FilterPolicyCheck:
    """Scan the effective git config for filter drivers and apply the policy.

    Runs ``git config --list -z`` (all scopes, include.path included) with
    the safe argv and sanitised environment; listing config never executes
    a filter driver. Strict mode refuses any non-LFS driver on every
    operation. Permissive mode warns and continues on read-only operations
    but still refuses non-LFS drivers on mutating ones. A config that
    cannot be listed fails closed, mirroring the pre-remote-op config scan.
    """
    active = resolve_filter_policy(policy, config=config)
    try:
        completed = subprocess.run(
            safe_git_argv(["config", "--list", "-z"]),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
            shell=False,
            env=git_environment(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return FilterPolicyCheck(
            allowed=False,
            message=(
                "Refused: repository Git config could not be inspected before "
                f"running Git ({exc}), so the filter policy cannot be verified."
            ),
        )
    if completed.returncode != 0:
        return FilterPolicyCheck(
            allowed=False,
            message=(
                "Refused: repository Git config could not be inspected before "
                "running Git (git config --list failed), so the filter policy "
                "cannot be verified."
            ),
        )
    # Entries are NUL-separated `key\\nvalue`; a valueless key has no value
    # and therefore no command to execute. Section/key matching is
    # case-insensitive; the driver (subsection) keeps its original case so
    # refusal messages name it exactly as configured.
    offenders: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in completed.stdout.split("\0"):
        if not entry:
            continue
        key, sep, value = entry.partition("\n")
        lowered = key.strip().lower()
        if not lowered.startswith("filter.") or not (
            lowered.endswith(".clean")
            or lowered.endswith(".smudge")
            or lowered.endswith(".process")
        ):
            continue
        command = value.strip() if sep else ""
        if not command:
            continue
        parts = key.strip().split(".")
        if len(parts) < 3:
            # `filter.clean` has no driver subsection and is not a filter
            # driver; only `filter.<driver>.clean|smudge|process` can run code.
            continue
        driver = ".".join(parts[1:-1])
        if driver in seen:
            continue
        seen.add(driver)
        if not _is_lfs_filter_command(command, exact=(active == "strict")):
            offenders.append((driver, command))
    if not offenders:
        return FilterPolicyCheck(allowed=True)
    driver, command = offenders[0]
    if active == "permissive" and read_only:
        drivers = ", ".join(f"'{d}'" for d, _ in offenders)
        return FilterPolicyCheck(
            allowed=True,
            warning=_warning_message(driver, command),
            message=(
                f"Git filter drivers {drivers} run under the permissive "
                f"read-only carve-out."
            ),
        )
    return FilterPolicyCheck(
        allowed=False,
        refused_driver=driver,
        refused_command=command,
        message=_refusal_message(driver, command, active, read_only=read_only),
    )


def sanitized_environment(
    base: Mapping[str, str] | None = None,
    *,
    private_home: str | os.PathLike[str] | None = None,
    temp_dir: str | os.PathLike[str] | None = None,
) -> dict[str, str]:
    """Return a subprocess environment with common credential variables stripped.

    This is defense in depth for build/verification commands. It does not isolate the
    child process from the filesystem or network and must not be described as an OS sandbox.
    """
    source = dict(os.environ if base is None else base)
    clean = {
        str(key): str(value)
        for key, value in source.items()
        if not _looks_sensitive_env_name(str(key))
    }
    clean["PYTHONNOUSERSITE"] = "1"
    clean["GIT_TERMINAL_PROMPT"] = "0"
    if private_home is not None:
        home = str(private_home)
        clean["HOME"] = home
        clean["USERPROFILE"] = home
        clean.pop("HOMEDRIVE", None)
        clean.pop("HOMEPATH", None)
        clean["XDG_CONFIG_HOME"] = str(Path(home) / ".config")
        clean["XDG_CACHE_HOME"] = str(Path(home) / ".cache")
        clean["XDG_DATA_HOME"] = str(Path(home) / ".local" / "share")
        clean["XDG_STATE_HOME"] = str(Path(home) / ".local" / "state")
    if temp_dir is not None:
        temp = str(temp_dir)
        clean["TMPDIR"] = temp
        clean["TMP"] = temp
        clean["TEMP"] = temp
    return clean


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
        return path.resolve(strict=False).relative_to(self.root).as_posix() or "."

    def chdir(self, path: str) -> Path:
        target = self.resolve(path)
        if not target.exists():
            target.mkdir(parents=True, exist_ok=True)
        if not target.is_dir():
            raise NotADirectoryError(path)
        self.cwd = target
        return target


class PermissionPolicy:
    """Hard tool-policy boundary independent of model instructions."""

    def __init__(self, mode: str = "build"):
        self.mode = mode if mode in VALID_MODES else "build"

    def validate(self, tool: str, args: dict[str, Any]) -> tuple[bool, str]:
        """Apply hard mode ceilings and input checks before capability policy."""

        if self.mode == "plan" and tool not in READ_ONLY_TOOLS:
            return False, f"'{tool}' is disabled in plan mode. Switch to /mode build or /mode yolo."
        if self.mode == "plan" and tool == "git_branch" and args.get("name"):
            return False, "Creating or switching branches is disabled in plan mode."

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
                option_value = normalized.split("=", 1)[1] if "=" in normalized else normalized
                if (
                    Path(option_value).is_absolute()
                    or option_value.startswith("/")
                    or option_value == ".."
                    or option_value.startswith("../")
                    or "/../" in option_value
                ):
                    return (
                        False,
                        "Absolute or parent-traversal command arguments require /mode yolo.",
                    )

        if exe == "git" and self.mode != "yolo":
            return False, "Git commands must use Sable's dedicated Git tools outside /mode yolo."

        if exe in DANGEROUS_EXECUTABLES and self.mode != "yolo":
            return False, f"Network/system command '{exe}' requires /mode yolo."

        if exe in PACKAGE_MANAGERS and self.mode != "yolo":
            # Running declared project scripts/checks is allowed; fetching or
            # mutating dependencies is not. Inspect all positions because package
            # managers accept global flags before their subcommand.
            mutating = {
                "install",
                "add",
                "remove",
                "uninstall",
                "update",
                "upgrade",
                "i",
                "exec",
                "dlx",
                "get",
                "fetch",
                "download",
                "publish",
                "login",
                "logout",
            }
            if any(token in mutating for token in lowered):
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

        if (
            exe == "node"
            and self.mode != "yolo"
            and any(flag in lowered for flag in ("-e", "--eval", "-p", "--print"))
        ):
            return False, "node eval/print execution is blocked outside /mode yolo."

        # Shell metacharacters should never be needed because subprocess uses shell=False.
        if any(any(c in token for c in (";", "&&", "||", "`", "$(")) for token in argv):
            return (
                False,
                "Shell syntax is not accepted by run_command; use argv items or /mode yolo + run_shell.",
            )

        return True, ""


def split_user_command(command: str) -> list[str]:
    return shlex.split(command, posix=True)
