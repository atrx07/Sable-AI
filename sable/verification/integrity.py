"""Lightweight heuristics for verifier-driven test-integrity regressions."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ..config import is_blocked_path, redact_secrets
from .affected import is_verification_config


IGNORE_DIRS = {".git", ".sable", ".venv", "venv", "node_modules", "target", "dist", "build", "__pycache__"}


class IntegrityStatus(str, Enum):
    CLEAR = "CLEAR"
    WARNING = "WARNING"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class TestMetrics:
    digest: str
    lines: int
    assertions: int
    pass_statements: int
    skip_markers: int
    blanket_skip_markers: int


@dataclass(frozen=True)
class IntegrityIssue:
    code: str
    path: str
    message: str
    blocking: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "path": self.path,
            "message": redact_secrets(self.message)[:500],
            "blocking": self.blocking,
        }


@dataclass(frozen=True)
class IntegrityReport:
    status: IntegrityStatus
    issues: tuple[IntegrityIssue, ...]
    baseline_test_count: int
    checked_paths: int

    @property
    def blocked(self) -> bool:
        return self.status == IntegrityStatus.BLOCKED

    @property
    def warnings(self) -> tuple[str, ...]:
        return tuple(f"{issue.path}: {issue.message}" for issue in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "blocked": self.blocked,
            "issues": [issue.to_dict() for issue in self.issues],
            "baseline_test_count": self.baseline_test_count,
            "checked_paths": self.checked_paths,
            "heuristic": True,
        }


class VerificationIntegrityBaseline:
    def __init__(
        self,
        root: Path,
        tests: dict[str, TestMetrics],
        scripts: dict[str, dict[str, str]],
        *,
        truncated: bool = False,
    ):
        self.root = root.resolve()
        self.tests = tests
        self.scripts = scripts
        self.truncated = truncated

    @staticmethod
    def _is_test(path: str) -> bool:
        name = Path(path).name.lower()
        return (
            name.startswith("test_") or name.endswith("_test.py")
            or ".test." in name or ".spec." in name
            or "/tests/" in f"/{path.lower()}" or "/__tests__/" in f"/{path.lower()}"
        )

    @staticmethod
    def _metrics(text: str) -> TestMetrics:
        lines = text.splitlines()
        assertions = len(re.findall(r"\bassert(?:Equal|True|False|Raises|In|NotIn|Is|IsNone|That)?\b|\bexpect\s*\(", text))
        pass_statements = len(re.findall(r"(?m)^\s*pass\s*(?:#.*)?$", text))
        skip_markers = len(re.findall(r"pytest\.mark\.(?:skip|xfail)|unittest\.skip|describe\.skip|test\.skip", text, re.IGNORECASE))
        blanket = len(re.findall(r"pytestmark\s*=\s*pytest\.mark\.(?:skip|xfail)|unittest\.skip\s*\([^)]*\)\s*\n\s*class\s+", text, re.IGNORECASE))
        return TestMetrics(
            hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest(),
            len(lines), assertions, pass_statements, skip_markers, blanket,
        )

    @staticmethod
    def _package_scripts(text: str) -> dict[str, str]:
        try:
            value = json.loads(text)
            scripts = value.get("scripts", {}) if isinstance(value, dict) else {}
            if not isinstance(scripts, dict):
                return {}
            return {
                name: str(command)[:1000]
                for name, command in scripts.items()
                if name in {"test", "lint", "typecheck", "check", "build"}
            }
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}

    @classmethod
    def capture(cls, root: str | Path, *, max_files: int = 500, max_file_bytes: int = 500_000) -> "VerificationIntegrityBaseline":
        workspace = Path(root).resolve()
        tests: dict[str, TestMetrics] = {}
        scripts: dict[str, dict[str, str]] = {}
        considered = 0
        truncated = False
        try:
            for current, dirnames, filenames in os.walk(workspace, followlinks=False):
                current_path = Path(current)
                dirnames[:] = sorted(name for name in dirnames if name not in IGNORE_DIRS)
                for name in sorted(filenames):
                    path = current_path / name
                    rel = path.relative_to(workspace).as_posix()
                    if is_blocked_path(rel) or not (cls._is_test(rel) or name == "package.json"):
                        continue
                    considered += 1
                    if considered > max_files:
                        truncated = True
                        break
                    try:
                        resolved = path.resolve(strict=True)
                        resolved.relative_to(workspace)
                        if resolved.stat().st_size > max_file_bytes:
                            truncated = True
                            continue
                        text = resolved.read_text(encoding="utf-8", errors="replace")
                    except (OSError, ValueError):
                        continue
                    if cls._is_test(rel):
                        tests[rel] = cls._metrics(text)
                    if name == "package.json":
                        scripts[rel] = cls._package_scripts(text)
                if considered > max_files:
                    break
        except OSError:
            truncated = True
        return cls(workspace, tests, scripts, truncated=truncated)

    @staticmethod
    def _explicit_test_intent(user_request: str) -> bool:
        test_noun = r"(?:test|tests|fixture|fixtures|assertion|assertions|coverage)"
        change_verb = r"(?:add|create|write|update|change|modify|fix|replace|refactor|maintain)"
        return bool(re.search(
            rf"(?:\b{change_verb}\b\s+(?:(?:the|an?|existing|new|regression)\s+)*\b{test_noun}\b|"
            rf"\b{test_noun}\b\s+(?:(?:must|should|needs?|is|are)\s+)?(?:be\s+)?"
            rf"(?:added|created|written|updated|changed|modified|fixed|replaced|refactored|maintained)\b)",
            user_request,
            re.IGNORECASE,
        ))

    @staticmethod
    def _explicit_test_deletion(user_request: str) -> bool:
        return bool(re.search(r"\b(delete|remove)\b.{0,40}\b(test|tests|fixture)\b", user_request, re.IGNORECASE))

    @staticmethod
    def _no_op(command: str) -> bool:
        value = " ".join(str(command).strip().lower().split())
        return value in {"true", ":", "pass", "exit 0"} or bool(re.fullmatch(r"echo(?:\s+['\"]?[^;&|]*['\"]?)?", value))

    def compare(self, changed_files: list[str], *, user_request: str, after_repair: bool) -> IntegrityReport:
        if not after_repair:
            return IntegrityReport(IntegrityStatus.CLEAR, (), len(self.tests), 0)
        changed = tuple(dict.fromkeys(str(path).replace("\\", "/") for path in changed_files))[:500]
        test_intent = self._explicit_test_intent(user_request)
        delete_intent = self._explicit_test_deletion(user_request)
        issues: list[IntegrityIssue] = []
        checked_paths = 0

        for path, baseline in self.tests.items():
            target = self.root / path
            checked_paths += 1
            if not target.exists():
                issues.append(IntegrityIssue(
                    "TEST_DELETED", path, "An existing test file was deleted during verifier-driven repair.",
                    blocking=not delete_intent,
                ))
                continue
            try:
                text = target.read_text(encoding="utf-8", errors="replace")[:500_000]
            except OSError:
                continue
            current = self._metrics(text)
            if current.digest == baseline.digest:
                continue
            if current.blanket_skip_markers > baseline.blanket_skip_markers:
                issues.append(IntegrityIssue(
                    "BLANKET_SKIP_ADDED", path, "A blanket skip/xfail marker was introduced during repair.", True,
                ))
            if baseline.assertions >= 2 and current.assertions * 2 < baseline.assertions:
                issues.append(IntegrityIssue(
                    "ASSERTIONS_REMOVED", path,
                    f"Assertions fell from {baseline.assertions} to {current.assertions}; review test intent.",
                    blocking=False,
                ))
            if baseline.lines >= 20 and current.lines * 2 < baseline.lines:
                issues.append(IntegrityIssue(
                    "TEST_CODE_REMOVED", path,
                    f"Test file shrank from {baseline.lines} to {current.lines} lines during repair.",
                    blocking=False,
                ))
            if current.pass_statements > baseline.pass_statements and current.assertions < baseline.assertions:
                issues.append(IntegrityIssue(
                    "ASSERTION_REPLACED_WITH_PASS", path,
                    "A pass statement appeared while assertions were removed during repair.",
                    blocking=not test_intent,
                ))

        for path in changed:
            if not is_verification_config(path):
                continue
            checked_paths += 1
            issues.append(IntegrityIssue(
                "VERIFICATION_CONFIG_CHANGED", path,
                "Verification configuration changed after a failure; the plan must be rediscovered.", False,
            ))
            if Path(path).name.lower() != "package.json" or path not in self.scripts:
                continue
            try:
                current_scripts = self._package_scripts((self.root / path).read_text(encoding="utf-8", errors="replace")[:100_000])
            except OSError:
                continue
            for name, old_command in self.scripts[path].items():
                new_command = current_scripts.get(name, "")
                if not self._no_op(old_command) and (not new_command or self._no_op(new_command)):
                    issues.append(IntegrityIssue(
                        "VERIFICATION_SCRIPT_DISABLED", path,
                        f"The '{name}' verification script was removed or replaced with a no-op.", True,
                    ))

        # Deduplicate stable issue identities while preserving deterministic order.
        unique = {(item.code, item.path): item for item in issues}
        ordered = tuple(unique[key] for key in sorted(unique))
        status = (
            IntegrityStatus.BLOCKED if any(item.blocking for item in ordered)
            else IntegrityStatus.WARNING if ordered
            else IntegrityStatus.CLEAR
        )
        return IntegrityReport(status, ordered, len(self.tests), checked_paths)
