"""Bounded, JSON-friendly repository context models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ..config import redact_secrets


@dataclass(frozen=True)
class SymbolInfo:
    file: str
    line: int
    end_line: int
    kind: str
    qualified_name: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ContextItem:
    path: str
    reasons: tuple[str, ...]
    score: int
    characters: int

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["reasons"] = list(self.reasons)
        return data


@dataclass
class RepositoryContext:
    workspace_root: str
    current_working_directory: str
    git: dict[str, Any] = field(default_factory=dict)
    languages: list[str] = field(default_factory=list)
    framework: str = "unknown"
    package_manager: str = "unknown"
    manifests: list[str] = field(default_factory=list)
    major_directories: list[str] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    test_files: list[str] = field(default_factory=list)
    recently_changed_files: list[str] = field(default_factory=list)
    recent_commits: list[str] = field(default_factory=list)
    symbols: list[SymbolInfo] = field(default_factory=list)
    imports: dict[str, list[str]] = field(default_factory=dict)
    importers: dict[str, list[str]] = field(default_factory=dict)
    test_relationships: dict[str, list[str]] = field(default_factory=dict)
    module_summaries: dict[str, str] = field(default_factory=dict)
    syntax_errors: list[str] = field(default_factory=list)
    repository_map: str = ""
    files: list[str] = field(default_factory=list)
    files_considered: int = 0
    files_indexed: int = 0
    truncated: bool = False
    duration_ms: int = 0

    def to_dict(self, *, include_symbols: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "workspace_root": self.workspace_root,
            "current_working_directory": self.current_working_directory,
            "git": dict(self.git),
            "languages": list(self.languages),
            "framework": self.framework,
            "package_manager": self.package_manager,
            "manifests": list(self.manifests),
            "major_directories": list(self.major_directories),
            "entry_points": list(self.entry_points),
            "test_files": list(self.test_files),
            "recently_changed_files": list(self.recently_changed_files),
            "recent_commits": list(self.recent_commits),
            "imports": {key: list(value) for key, value in self.imports.items()},
            "importers": {key: list(value) for key, value in self.importers.items()},
            "test_relationships": {key: list(value) for key, value in self.test_relationships.items()},
            "module_summaries": dict(self.module_summaries),
            "syntax_errors": list(self.syntax_errors),
            "repository_map": self.repository_map,
            "files_considered": self.files_considered,
            "files_indexed": self.files_indexed,
            "truncated": self.truncated,
            "duration_ms": self.duration_ms,
        }
        if include_symbols:
            data["symbols"] = [symbol.to_dict() for symbol in self.symbols]
        return data


@dataclass
class ContextSelection:
    request: str
    items: list[ContextItem]
    repository_map: str
    files_considered: int
    character_budget: int
    characters_used: int
    truncated: bool
    duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [item.to_dict() for item in self.items],
            "files_considered": self.files_considered,
            "files_selected": len(self.items),
            "character_budget": self.character_budget,
            "characters_used": self.characters_used,
            "approximated_tokens": (self.characters_used + 3) // 4,
            "truncated": self.truncated,
            "duration_ms": self.duration_ms,
        }

    def render_for_prompt(self) -> str:
        lines = ["DETERMINISTIC_REPOSITORY_CONTEXT", self.repository_map, "Relevant files:"]
        for item in self.items:
            lines.append(f"- {item.path} ({'; '.join(item.reasons)})")
        lines.append(
            f"Budget: {self.characters_used}/{self.character_budget} chars; "
            f"considered={self.files_considered}; selected={len(self.items)}; truncated={self.truncated}."
        )
        return redact_secrets("\n".join(lines))
