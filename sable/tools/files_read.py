"""Read-only filesystem and search tools."""

from __future__ import annotations

import glob
import os
from pathlib import Path

from ..config import is_blocked_path, redact_secrets
from .base import ToolResult


class ReadFileMixin:
    def read_file(self, path: str) -> ToolResult:
        target, denied = self._safe_path(path, "read_file")
        if denied:
            return denied
        assert target is not None
        try:
            if not target.is_file():
                return ToolResult("read_file", False, error=f"File not found: {path}")
            content = redact_secrets(target.read_text(errors="replace"))
            content, truncated = self._trim(content)
            return ToolResult("read_file", True, output=f"── {self._rel(target)} ──\n{content}", truncated=truncated)
        except OSError as exc:
            return ToolResult("read_file", False, error=str(exc))
    def read_file_lines(self, path: str, start: int = 1, end: int | None = None) -> ToolResult:
        target, denied = self._safe_path(path, "read_file_lines")
        if denied:
            return denied
        assert target is not None
        try:
            lines = target.read_text(errors="replace").splitlines(keepends=True)
            start = max(1, int(start))
            end = min(len(lines), int(end or len(lines)))
            selected = "".join(f"{idx:4d} │ {lines[idx-1]}" for idx in range(start, end + 1))
            selected, truncated = self._trim(selected)
            return ToolResult("read_file_lines", True, output=f"Lines {start}-{end} of {len(lines)}:\n{selected}", truncated=truncated)
        except OSError as exc:
            return ToolResult("read_file_lines", False, error=str(exc))
    def list_files(self, path: str = ".") -> ToolResult:
        target, denied = self._safe_path(path, "list_files")
        if denied:
            return denied
        assert target is not None
        if not target.exists():
            return ToolResult("list_files", False, error=f"Not found: {path}")
        lines: list[str] = []
        count = 0
        try:
            for root, dirs, files in os.walk(target):
                dirs[:] = [d for d in sorted(dirs) if d not in {".git", ".sable", "node_modules", "__pycache__", "venv", ".venv"}]
                root_path = Path(root)
                level = len(root_path.relative_to(target).parts)
                lines.append(f"{'  ' * level}📁 {root_path.name}/")
                for name in sorted(files):
                    if is_blocked_path(name):
                        continue
                    p = root_path / name
                    try:
                        size = p.stat().st_size
                    except OSError:
                        size = 0
                    lines.append(f"{'  ' * (level + 1)}📄 {name} ({size}B)")
                    count += 1
                    if count >= 500:
                        lines.append("... [listing capped at 500 files]")
                        return ToolResult("list_files", True, output="\n".join(lines), truncated=True)
            return ToolResult("list_files", True, output="\n".join(lines))
        except OSError as exc:
            return ToolResult("list_files", False, error=str(exc))
    def search_files(self, pattern: str, path: str = ".") -> ToolResult:
        target, denied = self._safe_path(path, "search_files")
        if denied:
            return denied
        assert target is not None
        try:
            matches = []
            for raw in glob.glob(str(target / "**" / pattern), recursive=True):
                p = Path(raw).resolve(strict=False)
                try:
                    rel = self.workspace.relative(p)
                except ValueError:
                    continue
                if is_blocked_path(rel) or any(part in {".git", ".sable", "node_modules", "venv", ".venv"} for part in p.parts):
                    continue
                matches.append(rel)
                if len(matches) >= 200:
                    break
            return ToolResult("search_files", True, output="\n".join(sorted(set(matches))) or f"No files matching '{pattern}'")
        except (OSError, ValueError) as exc:
            return ToolResult("search_files", False, error=str(exc))
    def grep_files(self, text: str, path: str = ".", ext: str = "") -> ToolResult:
        target, denied = self._safe_path(path, "grep_files")
        if denied:
            return denied
        assert target is not None
        matches: list[str] = []
        try:
            for p in target.rglob("*"):
                if len(matches) >= 200:
                    break
                if not p.is_file() or any(part in {".git", ".sable", "node_modules", "venv", ".venv", "__pycache__"} for part in p.parts):
                    continue
                if ext and p.suffix != ext:
                    continue
                rel = self._rel(p)
                if is_blocked_path(rel):
                    continue
                try:
                    for line_no, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                        if text in line:
                            matches.append(f"{rel}:{line_no}:{redact_secrets(line[:300])}")
                            if len(matches) >= 200:
                                break
                except OSError:
                    continue
            return ToolResult("grep_files", True, output="\n".join(matches) or "(no matches)", truncated=len(matches) >= 200)
        except OSError as exc:
            return ToolResult("grep_files", False, error=str(exc))
    def file_info(self, path: str) -> ToolResult:
        target, denied = self._safe_path(path, "file_info")
        if denied:
            return denied
        assert target is not None
        try:
            stat = target.stat()
            kind = "directory" if target.is_dir() else "file"
            return ToolResult("file_info", True, output=f"{kind}: {self._rel(target)}\nsize: {stat.st_size} bytes\nmode: {oct(stat.st_mode & 0o777)}")
        except OSError as exc:
            return ToolResult("file_info", False, error=str(exc))
    def disk_usage(self, path: str = ".") -> ToolResult:
        target, denied = self._safe_path(path, "disk_usage")
        if denied:
            return denied
        assert target is not None
        total = 0
        try:
            if target.is_file():
                total = target.stat().st_size
            else:
                for p in target.rglob("*"):
                    if p.is_file() and not any(part in {".git", "node_modules", "venv", ".venv"} for part in p.parts):
                        try:
                            total += p.stat().st_size
                        except OSError:
                            pass
            return ToolResult("disk_usage", True, output=f"{self._rel(target)}: {total / (1024 * 1024):.2f} MiB")
        except OSError as exc:
            return ToolResult("disk_usage", False, error=str(exc))
