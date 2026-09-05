"""Bounded read-only tools backed by the deterministic Context Engine."""

from __future__ import annotations

import json

from .base import ToolResult


class ContextToolMixin:
    def repo_map(self) -> ToolResult:
        context = self.context_engine.build(cwd=self.workspace.cwd)
        header = {
            "languages": context.languages,
            "framework": context.framework,
            "package_manager": context.package_manager,
            "files_considered": context.files_considered,
            "truncated": context.truncated,
        }
        output, truncated = self._trim(json.dumps(header, indent=2) + "\n" + context.repository_map)
        return ToolResult("repo_map", True, output=output, truncated=truncated)

    def list_symbols(self, path: str = "") -> ToolResult:
        relative = ""
        if path:
            target, denied = self._safe_path(path, "list_symbols")
            if denied:
                return denied
            assert target is not None
            relative = self._rel(target)
        symbols = [item.to_dict() for item in self.context_engine.find_symbols(path=relative)]
        output, truncated = self._trim(json.dumps(symbols, indent=2))
        return ToolResult("list_symbols", True, output=output, truncated=truncated)

    def find_symbol(self, name: str) -> ToolResult:
        symbols = [item.to_dict() for item in self.context_engine.find_symbols(name)]
        output, truncated = self._trim(json.dumps(symbols, indent=2))
        return ToolResult("find_symbol", True, output=output, truncated=truncated)

    def find_references(self, name: str) -> ToolResult:
        references = self.context_engine.find_references(name)
        output, truncated = self._trim(json.dumps(references, indent=2))
        return ToolResult("find_references", True, output=output, truncated=truncated)

    def read_symbol(self, name: str, path: str = "") -> ToolResult:
        relative = ""
        if path:
            target, denied = self._safe_path(path, "read_symbol")
            if denied:
                return denied
            assert target is not None
            relative = self._rel(target)
        exact = [
            symbol for symbol in self.context_engine.find_symbols(name, path=relative)
            if symbol.qualified_name == name or symbol.qualified_name.split(".")[-1] == name
        ]
        if not exact:
            return ToolResult("read_symbol", False, error=f"Symbol not found: {name}")
        if len(exact) > 1:
            choices = ", ".join(f"{item.file}:{item.line}" for item in exact[:10])
            return ToolResult("read_symbol", False, error=f"Symbol is ambiguous; specify path. Matches: {choices}")
        symbol = exact[0]
        target, denied = self._safe_path(symbol.file, "read_symbol")
        if denied:
            return denied
        assert target is not None
        try:
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            return ToolResult("read_symbol", False, error=str(exc))
        start = max(1, symbol.line - 2)
        end = min(len(lines), symbol.end_line + 2)
        selected = "\n".join(f"{number:4d} │ {lines[number - 1]}" for number in range(start, end + 1))
        output, truncated = self._trim(f"{symbol.file}:{start}-{end}\n{selected}")
        return ToolResult("read_symbol", True, output=output, truncated=truncated)

    def find_tests_for_file(self, path: str) -> ToolResult:
        target, denied = self._safe_path(path, "find_tests_for_file")
        if denied:
            return denied
        assert target is not None
        relative = self._rel(target)
        tests = self.context_engine.build().test_relationships.get(relative, [])
        output, truncated = self._trim(json.dumps(tests, indent=2))
        return ToolResult("find_tests_for_file", True, output=output, truncated=truncated)

    def recent_changes(self) -> ToolResult:
        context = self.context_engine.build()
        payload = {
            "git": context.git,
            "recently_changed_files": context.recently_changed_files,
            "recent_commits": context.recent_commits,
        }
        output, truncated = self._trim(json.dumps(payload, indent=2))
        return ToolResult("recent_changes", True, output=output, truncated=truncated)
