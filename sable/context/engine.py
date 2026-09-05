"""Deterministic, bounded repository discovery and Python context indexing."""

from __future__ import annotations

import ast
import os
import re
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from ..config import is_blocked_path, redact_secrets
from ..project import EXT_LANGUAGE, ProjectInspector
from .models import ContextItem, ContextSelection, RepositoryContext, SymbolInfo


IGNORE_DIRECTORIES = {
    ".git", ".sable", ".venv", "venv", "node_modules", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", "dist", "build",
    "target", "coverage", ".coverage",
}
ENTRY_POINT_NAMES = {"main.py", "app.py", "cli.py", "__main__.py", "manage.py", "index.js", "index.ts"}
STOP_WORDS = {
    "the", "and", "for", "with", "from", "this", "that", "into", "file", "files",
    "please", "could", "would", "should", "make", "change", "update", "add", "fix",
}


class ContextEngine:
    def __init__(
        self,
        root: str | Path,
        *,
        max_files: int = 3000,
        max_file_bytes: int = 1024 * 1024,
        map_character_budget: int = 6000,
        selection_character_budget: int = 12000,
        max_selected_files: int = 12,
    ):
        self.root = Path(root).expanduser().resolve()
        self.max_files = max(1, int(max_files))
        self.max_file_bytes = max(1024, int(max_file_bytes))
        self.map_character_budget = max(500, int(map_character_budget))
        self.selection_character_budget = max(500, int(selection_character_budget))
        self.max_selected_files = max(1, int(max_selected_files))
        self._cached_signature: tuple[Any, ...] | None = None
        self._cached_context: RepositoryContext | None = None

    def _safe_resolved(self, path: Path) -> bool:
        try:
            resolved = path.resolve(strict=False)
            rel = resolved.relative_to(self.root).as_posix()
        except (OSError, ValueError):
            return False
        return not is_blocked_path(rel)

    def _scan_files(self) -> tuple[list[Path], bool]:
        files: list[Path] = []
        truncated = False
        for base, dirs, names in os.walk(self.root, followlinks=False):
            base_path = Path(base)
            safe_dirs: list[str] = []
            for name in sorted(dirs):
                candidate = base_path / name
                try:
                    rel = candidate.relative_to(self.root).as_posix()
                except ValueError:
                    continue
                if name in IGNORE_DIRECTORIES or is_blocked_path(rel) or not self._safe_resolved(candidate):
                    continue
                safe_dirs.append(name)
            dirs[:] = safe_dirs
            for name in sorted(names):
                path = base_path / name
                try:
                    rel = path.relative_to(self.root).as_posix()
                except ValueError:
                    continue
                if is_blocked_path(rel) or not self._safe_resolved(path):
                    continue
                try:
                    if not path.is_file():
                        continue
                except OSError:
                    continue
                files.append(path)
                if len(files) >= self.max_files:
                    truncated = True
                    return files, truncated
        return files, truncated

    def _git(self, args: list[str]) -> str | None:
        try:
            result = subprocess.run(
                ["git", *args], cwd=str(self.root), capture_output=True, text=True,
                timeout=10, shell=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout if result.returncode == 0 else None

    def _git_paths(self, args: list[str]) -> list[str]:
        output = self._git(args)
        if output is None:
            return []
        paths = []
        for raw in output.split("\0"):
            rel = raw.strip().replace("\\", "/")
            if rel and not is_blocked_path(rel):
                paths.append(rel)
        return sorted(set(paths))[:200]

    def _git_context(self) -> tuple[dict[str, Any], list[str], list[str]]:
        root = self._git(["rev-parse", "--show-toplevel"])
        if not root:
            return {"present": False}, [], []
        try:
            repo_root = Path(root.strip()).resolve()
            repo_root.relative_to(self.root)
        except (OSError, ValueError):
            return {"present": False}, [], []
        branch = (self._git(["branch", "--show-current"]) or "").strip() or None
        head = (self._git(["rev-parse", "HEAD"]) or "").strip() or None
        staged = self._git_paths(["diff", "--cached", "--name-only", "-z", "--no-ext-diff"])
        unstaged = self._git_paths(["diff", "--name-only", "-z", "--no-ext-diff"])
        untracked = self._git_paths(["ls-files", "--others", "--exclude-standard", "-z"])
        recent = self._git_paths(["log", "-5", "--name-only", "-z", "--pretty=format:"])
        raw_commits = self._git(["log", "-5", "--pretty=format:%h%x09%s"]) or ""
        commits = [redact_secrets(line)[:300] for line in raw_commits.splitlines() if line.strip()]
        return {
            "present": True,
            "repo_root": str(repo_root),
            "branch": branch,
            "head": head,
            "staged": staged,
            "unstaged": unstaged,
            "untracked": untracked,
        }, recent, commits

    def _signature(self, files: list[Path], git_head: str | None) -> tuple[Any, ...]:
        values: list[Any] = [git_head, len(files)]
        for path in files:
            try:
                stat = path.stat()
                values.append((path.relative_to(self.root).as_posix(), stat.st_mtime_ns, stat.st_size))
            except (OSError, ValueError):
                continue
        return tuple(values)

    def _python_index(
        self, files: list[Path]
    ) -> tuple[list[SymbolInfo], dict[str, list[str]], dict[str, str], list[str]]:
        symbols: list[SymbolInfo] = []
        imports: dict[str, list[str]] = {}
        summaries: dict[str, str] = {}
        errors: list[str] = []
        for path in files:
            if path.suffix.lower() != ".py":
                continue
            rel = path.relative_to(self.root).as_posix()
            try:
                if path.stat().st_size > self.max_file_bytes:
                    errors.append(f"{rel}: file exceeds indexing limit")
                    continue
                source = path.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(source, filename=rel)
            except (OSError, SyntaxError) as exc:
                errors.append(f"{rel}: {redact_secrets(str(exc))[:200]}")
                continue
            docstring = ast.get_docstring(tree, clean=True)
            if docstring:
                summaries[rel] = redact_secrets(" ".join(docstring.split()))[:240]
            module_imports: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    module_imports.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    prefix = "." * node.level + (node.module or "")
                    module_imports.add(prefix or ".")
            imports[rel] = sorted(module_imports)

            def collect(body: list[ast.stmt], prefix: str = "") -> None:
                for node in body:
                    if isinstance(node, ast.ClassDef):
                        qualified = f"{prefix}.{node.name}" if prefix else node.name
                        symbols.append(SymbolInfo(rel, node.lineno, getattr(node, "end_lineno", node.lineno), "class", qualified))
                        collect(node.body, qualified)
                    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        qualified = f"{prefix}.{node.name}" if prefix else node.name
                        kind = "async_method" if prefix and isinstance(node, ast.AsyncFunctionDef) else (
                            "method" if prefix else ("async_function" if isinstance(node, ast.AsyncFunctionDef) else "function")
                        )
                        symbols.append(SymbolInfo(rel, node.lineno, getattr(node, "end_lineno", node.lineno), kind, qualified))
                        collect(node.body, qualified)

            collect(tree.body)
        return symbols, imports, summaries, errors

    @staticmethod
    def _module_name(path: str) -> str:
        parts = list(Path(path).with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts)

    def _importers(self, imports: dict[str, list[str]]) -> dict[str, list[str]]:
        known = {self._module_name(path): path for path in imports}
        reverse: dict[str, set[str]] = defaultdict(set)
        for source, targets in imports.items():
            for target in targets:
                normalized = target.lstrip(".")
                for module, path in known.items():
                    if normalized == module or normalized.startswith(module + ".") or module.endswith("." + normalized):
                        reverse[path].add(source)
        return {path: sorted(sources) for path, sources in reverse.items()}

    def _test_relationships(self, files: list[str], symbols: list[SymbolInfo]) -> dict[str, list[str]]:
        tests = [path for path in files if Path(path).name.startswith("test_") or "/tests/" in f"/{path}"]
        symbol_names: dict[str, list[str]] = defaultdict(list)
        for symbol in symbols:
            symbol_names[symbol.file].append(symbol.qualified_name.split(".")[-1])
        relationships: dict[str, list[str]] = {}
        for source in files:
            if source in tests:
                continue
            stem = Path(source).stem
            likely = [test for test in tests if Path(test).name in {f"test_{stem}.py", f"{stem}_test.py"}]
            if not likely and symbol_names.get(source):
                needles = symbol_names[source][:20]
                for test in tests[:200]:
                    try:
                        text = (self.root / test).read_text(errors="replace")[:100000]
                    except OSError:
                        continue
                    if any(re.search(rf"\b{re.escape(name)}\b", text) for name in needles):
                        likely.append(test)
            if likely:
                relationships[source] = sorted(set(likely))[:20]
        return relationships

    def _repo_map(self, files: list[str]) -> tuple[str, bool]:
        tree: dict[str, Any] = {}
        for path in files:
            cursor = tree
            for part in Path(path).parts:
                cursor = cursor.setdefault(part, {})
        lines = [f"{self.root.name}/"]
        truncated = False

        def render(node: dict[str, Any], prefix: str = "", depth: int = 0) -> None:
            nonlocal truncated
            if truncated or depth >= 4:
                return
            entries = sorted(node.items(), key=lambda item: (not bool(item[1]), item[0].lower()))
            for index, (name, children) in enumerate(entries):
                connector = "└── " if index == len(entries) - 1 else "├── "
                line = prefix + connector + name + ("/" if children else "")
                if sum(len(item) + 1 for item in lines) + len(line) + 1 > self.map_character_budget:
                    lines.append(prefix + "└── … [map truncated]")
                    truncated = True
                    return
                lines.append(line)
                if children:
                    render(children, prefix + ("    " if index == len(entries) - 1 else "│   "), depth + 1)

        render(tree)
        return "\n".join(lines), truncated

    def build(self, *, cwd: str | Path | None = None, force: bool = False) -> RepositoryContext:
        started = time.monotonic()
        files, scan_truncated = self._scan_files()
        git, recent, commits = self._git_context()
        resolved_cwd = Path(cwd or self.root).resolve()
        signature = (str(resolved_cwd),) + self._signature(files, git.get("head"))
        if not force and self._cached_context is not None and signature == self._cached_signature:
            return self._cached_context
        relative = [path.relative_to(self.root).as_posix() for path in files]
        profile = ProjectInspector(self.root).profile()
        symbols, imports, summaries, syntax_errors = self._python_index(files)
        repository_map, map_truncated = self._repo_map(relative)
        major = sorted({Path(path).parts[0] for path in relative if len(Path(path).parts) > 1})[:40]
        entry_points = [
            path for path in relative
            if Path(path).name in ENTRY_POINT_NAMES or path in {"pyproject.toml", "package.json", "Cargo.toml", "go.mod"}
        ][:40]
        tests = [path for path in relative if Path(path).name.startswith("test_") or "/tests/" in f"/{path}"][:300]
        context = RepositoryContext(
            workspace_root=str(self.root),
            current_working_directory=str(resolved_cwd),
            git=git,
            languages=list(profile["languages"]),
            framework=str(profile["framework"]),
            package_manager=str(profile["package_manager"]),
            manifests=list(profile["manifests"]),
            major_directories=major,
            entry_points=entry_points,
            test_files=tests,
            recently_changed_files=recent,
            recent_commits=commits,
            symbols=symbols,
            imports=imports,
            importers=self._importers(imports),
            test_relationships=self._test_relationships(relative, symbols),
            module_summaries=summaries,
            syntax_errors=syntax_errors,
            repository_map=repository_map,
            files=relative,
            files_considered=len(relative),
            files_indexed=len(imports),
            truncated=scan_truncated or map_truncated,
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
        )
        self._cached_signature = signature
        self._cached_context = context
        return context

    @staticmethod
    def _request_terms(request: str) -> set[str]:
        return {
            term for term in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", request.lower())
            if term not in STOP_WORDS
        }

    def select(self, request: str, *, cwd: str | Path | None = None) -> ContextSelection:
        started = time.monotonic()
        context = self.build(cwd=cwd)
        selection_map_budget = max(250, int(self.selection_character_budget * 0.6))
        repository_map = context.repository_map
        map_trimmed = len(repository_map) > selection_map_budget
        if map_trimmed:
            repository_map = repository_map[: max(0, selection_map_budget - 18)].rstrip() + "\n… [map trimmed]"
        terms = self._request_terms(request)
        dirty = set(context.git.get("staged", []) + context.git.get("unstaged", []) + context.git.get("untracked", []))
        recent = set(context.recently_changed_files)
        symbols_by_file: dict[str, list[str]] = defaultdict(list)
        for symbol in context.symbols:
            symbols_by_file[symbol.file].append(symbol.qualified_name.lower())
        scored: list[tuple[int, str, list[str]]] = []
        content_matches: dict[str, list[str]] = {}
        search_bytes_left = 2 * 1024 * 1024
        searchable_suffixes = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".md", ".toml", ".json", ".yaml", ".yml"}
        if terms:
            for path in context.files:
                candidate = self.root / path
                if candidate.suffix.lower() not in searchable_suffixes or search_bytes_left <= 0:
                    continue
                try:
                    size = min(candidate.stat().st_size, 65536, search_bytes_left)
                    if size <= 0:
                        continue
                    with candidate.open("r", encoding="utf-8", errors="replace") as handle:
                        text = handle.read(size).lower()
                    search_bytes_left -= size
                except OSError:
                    continue
                matched = sorted(term for term in terms if re.search(rf"\b{re.escape(term)}\b", text))
                if matched:
                    content_matches[path] = matched[:4]
        for path in context.files:
            path_lower = path.lower()
            reasons: list[str] = []
            score = 0
            matched_path = sorted(term for term in terms if term in path_lower)
            if matched_path:
                score += 10 * len(matched_path)
                reasons.append("path matches " + ", ".join(matched_path[:4]))
            matched_symbols = sorted({term for term in terms if any(term in name for name in symbols_by_file.get(path, []))})
            if matched_symbols:
                score += 8 * len(matched_symbols)
                reasons.append("symbol matches " + ", ".join(matched_symbols[:4]))
            if path in dirty:
                score += 5
                reasons.append("dirty in Git")
            if path in recent:
                score += 2
                reasons.append("recently committed")
            if path in context.entry_points:
                score += 1
                reasons.append("entry point or manifest")
            if path in content_matches:
                score += 3 * len(content_matches[path])
                reasons.append("content mentions " + ", ".join(content_matches[path]))
            if score:
                scored.append((score, path, reasons))
        expanded: dict[str, tuple[int, list[str]]] = {
            path: (score, list(reasons)) for score, path, reasons in scored
        }
        for score, path, _reasons in list(scored):
            related_paths: list[tuple[str, str]] = []
            related_paths.extend((item, f"likely test for {path}") for item in context.test_relationships.get(path, []))
            related_paths.extend((item, f"imports {path}") for item in context.importers.get(path, []))
            for target, importers in context.importers.items():
                if path in importers:
                    related_paths.append((target, f"imported by {path}"))
            for related, reason in related_paths:
                previous_score, previous_reasons = expanded.get(related, (0, []))
                if reason not in previous_reasons:
                    previous_reasons.append(reason)
                expanded[related] = (max(previous_score, min(score, 4)), previous_reasons)
        scored = [(score, path, reasons) for path, (score, reasons) in expanded.items()]
        if not scored:
            for path in context.entry_points[: self.max_selected_files]:
                scored.append((1, path, ["repository entry point"] ))
        scored.sort(key=lambda item: (-item[0], item[1]))
        items: list[ContextItem] = []
        used = len(repository_map)
        truncated = context.truncated or map_trimmed
        for score, path, reasons in scored:
            related = context.test_relationships.get(path, [])
            if related:
                reasons = reasons + ["likely tests: " + ", ".join(related[:3])]
            cost = len(path) + sum(len(reason) for reason in reasons) + 8
            if len(items) >= self.max_selected_files or used + cost > self.selection_character_budget:
                truncated = True
                continue
            items.append(ContextItem(path, tuple(reasons), score, cost))
            used += cost
        return ContextSelection(
            request=redact_secrets(str(request))[:500],
            items=items,
            repository_map=repository_map,
            files_considered=context.files_considered,
            character_budget=self.selection_character_budget,
            characters_used=min(used, self.selection_character_budget),
            truncated=truncated,
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
        )

    def find_symbols(self, query: str = "", *, path: str = "", limit: int = 100) -> list[SymbolInfo]:
        context = self.build()
        needle = query.lower().strip()
        return [
            symbol for symbol in context.symbols
            if (not needle or needle in symbol.qualified_name.lower()) and (not path or symbol.file == path)
        ][:max(1, min(200, int(limit)))]

    def find_references(self, symbol_name: str, *, limit: int = 100) -> list[dict[str, Any]]:
        if not symbol_name or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", symbol_name):
            return []
        pattern = re.compile(rf"\b{re.escape(symbol_name)}\b")
        matches: list[dict[str, Any]] = []
        for rel in self.build().files:
            if not rel.endswith(".py"):
                continue
            path = self.root / rel
            try:
                if path.stat().st_size > self.max_file_bytes:
                    continue
                lines = path.read_text(errors="replace").splitlines()
            except OSError:
                continue
            for line_no, line in enumerate(lines, 1):
                if pattern.search(line):
                    matches.append({"file": rel, "line": line_no, "excerpt": redact_secrets(line.strip())[:240]})
                    if len(matches) >= max(1, min(200, int(limit))):
                        return matches
        return matches
