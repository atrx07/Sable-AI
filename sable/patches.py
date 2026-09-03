"""Strict unified-diff parsing and deterministic in-memory application."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


class PatchError(ValueError):
    """Raised when a patch is malformed or does not match its target content."""


@dataclass
class PatchHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class FilePatch:
    old_path: str | None
    new_path: str | None
    hunks: list[PatchHunk] = field(default_factory=list)

    @property
    def path(self) -> str:
        path = self.new_path or self.old_path
        if not path:
            raise PatchError("Patch has no target path.")
        return path

    @property
    def operation(self) -> str:
        if self.old_path is None:
            return "create"
        if self.new_path is None:
            return "delete"
        return "update"


HUNK_HEADER = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:.*?)(?:\r?\n)?$"
)


def _header_path(line: str, prefix: str, side: str) -> str | None:
    if not line.startswith(prefix):
        raise PatchError(f"Expected {side} file header.")
    raw = line[len(prefix):].rstrip("\r\n").split("\t", 1)[0]
    if raw == "/dev/null":
        return None
    if not raw or raw.startswith('"'):
        raise PatchError("Empty and quoted patch paths are not supported.")
    expected = "a/" if side == "old" else "b/"
    return raw[len(expected):] if raw.startswith(expected) else raw


def parse_unified_diff(text: str) -> list[FilePatch]:
    """Parse a conventional unified diff, rejecting ambiguous extra content."""
    if not isinstance(text, str) or not text.strip():
        raise PatchError("Patch is empty.")
    lines = text.splitlines(keepends=True)
    patches: list[FilePatch] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith(("diff --git ", "index ", "new file mode ", "deleted file mode ", "old mode ", "new mode ")) or not line.strip():
            index += 1
            continue
        if not line.startswith("--- "):
            raise PatchError(f"Unexpected patch content at line {index + 1}: {line.rstrip()[:80]}")
        old_path = _header_path(line, "--- ", "old")
        index += 1
        if index >= len(lines):
            raise PatchError("Patch is missing its new-file header.")
        new_path = _header_path(lines[index], "+++ ", "new")
        index += 1
        if old_path is None and new_path is None:
            raise PatchError("Both patch paths cannot be /dev/null.")
        if old_path and new_path and old_path != new_path:
            raise PatchError("Rename patches are not supported; use move_file explicitly.")
        file_patch = FilePatch(old_path, new_path)
        while index < len(lines) and lines[index].startswith("@@ "):
            match = HUNK_HEADER.match(lines[index])
            if not match:
                raise PatchError(f"Malformed hunk header at line {index + 1}.")
            old_start, old_count, new_start, new_count = (
                int(match.group(1)), int(match.group(2) or "1"),
                int(match.group(3)), int(match.group(4) or "1"),
            )
            hunk = PatchHunk(old_start, old_count, new_start, new_count)
            index += 1
            old_seen = new_seen = 0
            while old_seen < old_count or new_seen < new_count:
                if index >= len(lines):
                    raise PatchError("Hunk ended before its declared line counts were satisfied.")
                body = lines[index]
                if body.startswith("\\ No newline at end of file"):
                    if not hunk.lines:
                        raise PatchError("No-newline marker has no preceding patch line.")
                    op, payload = hunk.lines[-1]
                    hunk.lines[-1] = (op, payload.rstrip("\r\n"))
                    index += 1
                    continue
                if not body or body[0] not in " +-":
                    raise PatchError(f"Invalid hunk line at line {index + 1}.")
                op, payload = body[0], body[1:]
                hunk.lines.append((op, payload))
                old_seen += op in " -"
                new_seen += op in " +"
                if old_seen > old_count or new_seen > new_count:
                    raise PatchError("Hunk contains more lines than its header declares.")
                index += 1
            if index < len(lines) and lines[index].startswith("\\ No newline at end of file"):
                if not hunk.lines:
                    raise PatchError("No-newline marker has no preceding patch line.")
                op, payload = hunk.lines[-1]
                hunk.lines[-1] = (op, payload.rstrip("\r\n"))
                index += 1
            file_patch.hunks.append(hunk)
        if not file_patch.hunks:
            raise PatchError(f"Patch for {file_patch.path} has no hunks.")
        patches.append(file_patch)
    if not patches:
        raise PatchError("Patch contains no file changes.")
    paths = [patch.path for patch in patches]
    if len(set(paths)) != len(paths):
        raise PatchError("A patch may target each file only once.")
    return patches


def apply_file_patch(patch: FilePatch, original: str | None) -> str | None:
    """Apply one parsed file patch to text after validating every context line."""
    if patch.operation == "create":
        if original is not None:
            raise PatchError(f"Create target already exists: {patch.path}")
        source: list[str] = []
    else:
        if original is None:
            raise PatchError(f"Patch target does not exist: {patch.path}")
        source = original.splitlines(keepends=True)

    output: list[str] = []
    cursor = 0
    for hunk_number, hunk in enumerate(patch.hunks, 1):
        old_index = 0 if hunk.old_start == 0 else hunk.old_start - 1
        new_index = 0 if hunk.new_start == 0 else hunk.new_start - 1
        if old_index < cursor:
            raise PatchError(f"Overlapping or out-of-order hunk {hunk_number} for {patch.path}.")
        if old_index > len(source):
            raise PatchError(f"Hunk {hunk_number} starts beyond the end of {patch.path}.")
        output.extend(source[cursor:old_index])
        cursor = old_index
        if new_index != len(output):
            raise PatchError(f"Invalid new-file start in hunk {hunk_number} for {patch.path}.")
        for op, payload in hunk.lines:
            if op in " -":
                if cursor >= len(source) or source[cursor] != payload:
                    actual = "<EOF>" if cursor >= len(source) else source[cursor].rstrip("\r\n")
                    raise PatchError(
                        f"Context mismatch in {patch.path} hunk {hunk_number}: "
                        f"expected {payload.rstrip()!r}, found {actual!r}."
                    )
                if op == " ":
                    output.append(source[cursor])
                cursor += 1
            else:
                output.append(payload)
    output.extend(source[cursor:])
    updated = "".join(output)
    if patch.operation == "delete":
        if updated:
            raise PatchError(f"Delete patch did not remove all content from {patch.path}.")
        return None
    return updated
