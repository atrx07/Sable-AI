"""Canonical fixture copying and baseline snapshots for evaluation runs."""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


MAX_FIXTURE_FILES = 500
MAX_FIXTURE_BYTES = 5 * 1024 * 1024


def snapshot_tree(root: Path) -> dict[str, str]:
    """Return bounded relative-path SHA-256 fingerprints without following links."""
    snapshot: dict[str, str] = {}
    total = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"fixture contains a symbolic link: {path.relative_to(root).as_posix()}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        data = path.read_bytes()
        total += len(data)
        if len(snapshot) >= MAX_FIXTURE_FILES or total > MAX_FIXTURE_BYTES:
            raise ValueError("fixture exceeds the bounded file or byte budget")
        snapshot[relative] = hashlib.sha256(data).hexdigest()
    return snapshot


@dataclass(frozen=True)
class MaterializedFixture:
    path: Path
    baseline: dict[str, str]


class FixtureManager:
    def __init__(self, fixtures_root: str | Path):
        self.fixtures_root = Path(fixtures_root).resolve()
        if not self.fixtures_root.is_dir():
            raise ValueError(f"fixture root does not exist: {self.fixtures_root}")

    def _source(self, fixture: str) -> Path:
        requested = Path(fixture)
        if requested.is_absolute() or ".." in requested.parts:
            raise ValueError("fixture path must remain inside the fixture root")
        source = (self.fixtures_root / requested).resolve()
        try:
            source.relative_to(self.fixtures_root)
        except ValueError as exc:
            raise ValueError("fixture path escapes the fixture root") from exc
        if not source.is_dir():
            raise ValueError(f"fixture does not exist: {fixture}")
        return source

    @contextmanager
    def materialize(self, fixture: str) -> Iterator[MaterializedFixture]:
        source = self._source(fixture)
        baseline = snapshot_tree(source)
        with tempfile.TemporaryDirectory(prefix="sable-eval-") as temp_root:
            destination = Path(temp_root, "workspace")
            shutil.copytree(source, destination, symlinks=True)
            copied = snapshot_tree(destination)
            if copied != baseline:
                raise ValueError("materialized fixture does not match its canonical source")
            yield MaterializedFixture(destination, baseline)


__all__ = ["FixtureManager", "MaterializedFixture", "snapshot_tree"]
