"""Deterministic, bounded verification failure intelligence."""

from __future__ import annotations

import hashlib
import re

from ..config import redact_secrets
from ..tools import ToolResult
from .models import CheckCategory, CheckStatus, FailureClassification, VerificationCheck


RELEVANT_LINE = re.compile(
    r"(error|failed|failure|exception|traceback|assert|cannot|missing|not found|timeout|denied|blocked|warning)",
    re.IGNORECASE,
)


class FailureClassifier:
    @staticmethod
    def diagnostic(result: ToolResult, status: CheckStatus, *, limit: int = 4000) -> str:
        text = redact_secrets("\n".join(part for part in (result.error, result.output) if part)).strip()
        if not text:
            text = status.value.replace("_", " ").title()
        lines = text.splitlines()
        relevant = [line for line in lines if RELEVANT_LINE.search(line)][:24]
        chosen = list(dict.fromkeys([*lines[:8], *relevant, *lines[-8:]]))
        excerpt = "\n".join(chosen)
        if len(excerpt) <= limit:
            return excerpt
        marker = "\n... [diagnostic truncated] ...\n"
        remaining = max(0, limit - len(marker))
        left = remaining // 2
        return excerpt[:left] + marker + excerpt[-(remaining - left):]

    @staticmethod
    def classify(check: VerificationCheck, status: CheckStatus, result: ToolResult, diagnostic: str) -> FailureClassification:
        if status == CheckStatus.PASS:
            return FailureClassification.NONE
        if status == CheckStatus.TIMEOUT:
            return FailureClassification.TIMEOUT
        if status in {CheckStatus.BLOCKED, CheckStatus.SKIPPED_POLICY}:
            return FailureClassification.POLICY_BLOCKED
        if status == CheckStatus.SKIPPED_UNAVAILABLE:
            return FailureClassification.TOOL_MISSING
        text = diagnostic.lower()
        if re.search(r"error collecting|collection error|failed to collect", text):
            return FailureClassification.TEST_COLLECTION_FAILURE
        if re.search(r"no module named|cannot find module|could not resolve dependenc|package .* not found|dependency .* missing", text):
            return FailureClassification.DEPENDENCY_MISSING
        if re.search(r"syntaxerror|indentationerror|unexpected token|parse error", text):
            return FailureClassification.SYNTAX_ERROR
        if re.search(r"importerror|cannot import name|unresolved import", text):
            return FailureClassification.IMPORT_ERROR
        if re.search(r"assertionerror|\bfailed\b.*::|expected .*(?:but|got|actual)", text):
            return FailureClassification.ASSERTION_FAILURE
        if re.search(r"\bbuild (?:error|failed|failure)\b", text):
            return FailureClassification.BUILD_ERROR
        if re.search(r"out of memory|memoryerror|no space left|resource temporarily unavailable", text):
            return FailureClassification.RESOURCE_LIMIT
        if check.category == CheckCategory.TYPECHECK or re.search(r"type error|incompatible type|is not assignable to", text):
            return FailureClassification.TYPE_ERROR
        if check.category in {CheckCategory.LINT, CheckCategory.FORMAT, CheckCategory.STATIC_ANALYSIS}:
            return FailureClassification.LINT_ERROR
        if check.category in {CheckCategory.BUILD, CheckCategory.PACKAGE}:
            return FailureClassification.BUILD_ERROR
        return FailureClassification.UNKNOWN_FAILURE

    @staticmethod
    def signature(check: VerificationCheck, classification: FailureClassification, diagnostic: str) -> str:
        if classification == FailureClassification.NONE:
            return ""
        normalized = diagnostic.lower()
        normalized = re.sub(r"[a-f0-9]{12,}", "<hex>", normalized)
        normalized = re.sub(r"(?<=:)(?:\d+)(?::\d+)?", "<line>", normalized)
        normalized = re.sub(r"\bline\s+\d+\b", "line <line>", normalized)
        normalized = re.sub(r"\b\d+(?:\.\d+)?\s*(?:ms|s|sec|seconds|minutes)\b", "<duration>", normalized)
        normalized = re.sub(r"[ \t]+", " ", normalized).strip()[:4000]
        material = f"{check.check_id}\n{classification.value}\n{normalized}"
        return "failure-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]

    def analyze(self, check: VerificationCheck, status: CheckStatus, result: ToolResult) -> tuple[FailureClassification, str, str]:
        diagnostic = self.diagnostic(result, status)
        classification = self.classify(check, status, result, diagnostic)
        return classification, diagnostic, self.signature(check, classification, diagnostic)
