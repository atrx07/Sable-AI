"""Bounded project-root discovery for verification adapters."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .adapters import DEFAULT_ADAPTERS, AvailabilityResolver, VerificationAdapter
from .models import VerificationCheck, VerificationScope


IGNORE_DIRS = {
    ".git", ".sable", ".venv", "venv", "node_modules", "target", "dist",
    "build", ".gradle", ".idea", "__pycache__",
}
PRIMARY_MANIFESTS = frozenset(
    name for adapter in DEFAULT_ADAPTERS for name in adapter.primary_manifests
) | frozenset({"requirements.txt", "requirements-dev.txt", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "Cargo.lock", "go.sum"})


def _is_manifest_name(name: str) -> bool:
    lowered = name.lower()
    return (
        name in PRIMARY_MANIFESTS
        or lowered.startswith("requirements") and lowered.endswith(".txt")
        or lowered.startswith("eslint.config.")
        or lowered.startswith(".eslintrc")
        or lowered.startswith(".prettierrc")
        or lowered.startswith("vite.config.")
        or lowered.startswith("vitest.config.")
        or lowered.startswith("jest.config.")
    )


@dataclass(frozen=True)
class DiscoveryResult:
    checks: tuple[VerificationCheck, ...]
    manifests: tuple[str, ...]
    project_roots: tuple[str, ...]
    adapters: tuple[str, ...]
    warnings: tuple[str, ...]
    roots_avoided: int = 0


class VerificationDiscovery:
    """Find relevant manifest roots without executing repository code."""

    def __init__(
        self,
        root: str | Path,
        *,
        executable_finder: Callable[[str], str | None] = shutil.which,
        max_manifests: int = 200,
        max_depth: int = 6,
    ):
        self.root = Path(root).resolve()
        self.executable_finder = executable_finder
        self.max_manifests = max(1, int(max_manifests))
        self.max_depth = max(1, int(max_depth))

    def _scan(self) -> tuple[list[Path], list[str], list[str]]:
        roots: set[Path] = set()
        manifests: list[str] = []
        warnings: list[str] = []
        saw_python = False
        try:
            for current, dirnames, filenames in os.walk(self.root):
                current_path = Path(current)
                depth = len(current_path.relative_to(self.root).parts)
                dirnames[:] = sorted(
                    name for name in dirnames
                    if name not in IGNORE_DIRS and depth < self.max_depth
                )
                for name in sorted(filenames):
                    saw_python = saw_python or name.endswith(".py")
                    if not _is_manifest_name(name):
                        continue
                    path = current_path / name
                    manifests.append(path.relative_to(self.root).as_posix())
                    roots.add(current_path)
                    if len(manifests) >= self.max_manifests:
                        warnings.append(f"Manifest scan reached its {self.max_manifests}-file budget.")
                        return sorted(roots), sorted(manifests), warnings
        except OSError as exc:
            warnings.append(f"Manifest scan was incomplete: {exc}")
        if not roots:
            # Preserve the old source-only fallback without treating every extension as a project root.
            if saw_python or (self.root / "tests").is_dir():
                roots.add(self.root)
        return sorted(roots), sorted(manifests), warnings

    def _relevant_roots(
        self,
        roots: list[Path],
        changed_files: tuple[str, ...],
        scope: VerificationScope,
    ) -> list[Path]:
        if scope == VerificationScope.FULL or not changed_files:
            return roots
        selected: set[Path] = set()
        for changed in changed_files:
            candidate = (self.root / changed).resolve()
            ancestors = [root for root in roots if candidate == root or root in candidate.parents]
            if ancestors:
                selected.add(max(ancestors, key=lambda path: len(path.parts)))
        return sorted(selected)

    def discover(
        self,
        changed_files: list[str] | tuple[str, ...],
        *,
        scope: VerificationScope | str = VerificationScope.AFFECTED,
    ) -> DiscoveryResult:
        requested_scope = VerificationScope.parse(scope)
        normalized = tuple(sorted(dict.fromkeys(
            str(path).replace("\\", "/") for path in changed_files if str(path).strip()
        )))[:500]
        roots, manifests, warnings = self._scan()
        relevant = self._relevant_roots(roots, normalized, requested_scope)
        resolver = AvailabilityResolver(self.root, self.executable_finder)
        checks: list[VerificationCheck] = []
        used_adapters: set[str] = set()
        for project_root in relevant:
            for adapter_type in DEFAULT_ADAPTERS:
                adapter: VerificationAdapter = adapter_type(self.root, project_root, resolver)
                discovered = adapter.discover(normalized)
                if discovered:
                    checks.extend(discovered)
                    used_adapters.add(adapter_type.__name__)
                warnings.extend(adapter.warnings)
        roots_display = tuple(path.relative_to(self.root).as_posix() or "." for path in relevant)
        return DiscoveryResult(
            checks=tuple(checks),
            manifests=tuple(manifests),
            project_roots=roots_display,
            adapters=tuple(sorted(used_adapters)),
            warnings=tuple(dict.fromkeys(warnings)),
            roots_avoided=max(0, len(roots) - len(relevant)),
        )
