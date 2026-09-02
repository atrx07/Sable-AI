"""Reversible local workspace transactions for Sable tasks.

Transactions snapshot paths immediately before Sable mutates them. Snapshots are
kept in a private temporary directory that is never exposed as a model tool path.
The most recently completed transaction can be restored with ``/undo`` without
rewriting Git history.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path


class TransactionError(RuntimeError):
    """Raised when Sable cannot create or restore a safe transaction snapshot."""


@dataclass
class PathSnapshot:
    relative_path: str
    existed: bool
    kind: str = "missing"
    backup_path: Path | None = None
    link_target: str | None = None


@dataclass
class WorkspaceTransaction:
    transaction_id: str
    label: str
    backup_dir: Path
    snapshots: list[PathSnapshot] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    backup_bytes: int = 0


class WorkspaceTransactionManager:
    """Capture pre-mutation workspace state and restore the latest task on demand."""

    def __init__(
        self,
        root: str | Path,
        *,
        max_backup_bytes: int = 25 * 1024 * 1024,
        max_entries: int = 4000,
    ):
        self.root = Path(root).expanduser().resolve()
        self.max_backup_bytes = max(1, int(max_backup_bytes))
        self.max_entries = max(1, int(max_entries))
        self.current: WorkspaceTransaction | None = None
        self.last: WorkspaceTransaction | None = None

    def begin(self, label: str = "task") -> str:
        if self.current is not None:
            raise TransactionError("A workspace transaction is already active.")
        txid = uuid.uuid4().hex[:12]
        backup_dir = Path(tempfile.mkdtemp(prefix=f"sable-txn-{txid}-"))
        self.current = WorkspaceTransaction(txid, str(label or "task")[:200], backup_dir)
        return txid

    def _relative(self, target: Path) -> str:
        resolved = target.resolve(strict=False)
        try:
            return resolved.relative_to(self.root).as_posix()
        except ValueError as exc:
            raise TransactionError(f"Transaction target escapes workspace: {target}") from exc

    @staticmethod
    def _is_same_or_parent(parent: str, child: str) -> bool:
        if parent == child:
            return True
        prefix = parent.rstrip("/") + "/"
        return child.startswith(prefix)

    def _measure(self, target: Path) -> tuple[int, int]:
        if target.is_symlink():
            return 0, 1
        if target.is_file():
            try:
                return target.stat().st_size, 1
            except OSError as exc:
                raise TransactionError(f"Could not inspect {target}: {exc}") from exc
        if target.is_dir():
            total = 0
            entries = 1
            try:
                for path in target.rglob("*"):
                    entries += 1
                    if entries > self.max_entries:
                        raise TransactionError(
                            f"Transaction snapshot exceeds {self.max_entries} filesystem entries."
                        )
                    if path.is_file() and not path.is_symlink():
                        total += path.stat().st_size
                        if total > self.max_backup_bytes:
                            break
            except OSError as exc:
                raise TransactionError(f"Could not inspect {target}: {exc}") from exc
            return total, entries
        return 0, 1

    def capture(self, target: str | Path) -> None:
        """Snapshot a path before its first relevant mutation in the active task."""
        if self.current is None:
            return

        path = Path(target).expanduser().resolve(strict=False)
        rel = self._relative(path)

        # An earlier parent snapshot already contains this path's pre-task state.
        if any(self._is_same_or_parent(snapshot.relative_path, rel) for snapshot in self.current.snapshots):
            return

        existed = path.exists() or path.is_symlink()
        snapshot = PathSnapshot(relative_path=rel, existed=existed)

        if existed:
            size, _ = self._measure(path)
            projected = self.current.backup_bytes + size
            if projected > self.max_backup_bytes:
                raise TransactionError(
                    "Refused transactional mutation: backup budget would exceed "
                    f"{self.max_backup_bytes // (1024 * 1024)} MiB."
                )

            slot = self.current.backup_dir / f"{len(self.current.snapshots):04d}"
            if path.is_symlink():
                snapshot.kind = "symlink"
                snapshot.link_target = os.readlink(path)
            elif path.is_dir():
                snapshot.kind = "directory"
                shutil.copytree(path, slot, symlinks=True)
                snapshot.backup_path = slot
            else:
                snapshot.kind = "file"
                slot.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, slot, follow_symlinks=False)
                snapshot.backup_path = slot
            self.current.backup_bytes = projected

        self.current.snapshots.append(snapshot)

    @staticmethod
    def _remove_current(target: Path) -> None:
        if target.is_symlink() or target.is_file():
            target.unlink(missing_ok=True)
        elif target.is_dir():
            shutil.rmtree(target)

    def _restore(self, transaction: WorkspaceTransaction) -> list[str]:
        restored: list[str] = []
        errors: list[str] = []

        # Reverse capture order is important when a later operation snapshots a
        # parent after an earlier operation already changed one of its children.
        for snapshot in reversed(transaction.snapshots):
            target = (self.root / snapshot.relative_path).resolve(strict=False)
            try:
                target.relative_to(self.root)
            except ValueError:
                errors.append(f"unsafe restore path: {snapshot.relative_path}")
                continue

            try:
                self._remove_current(target)
                if snapshot.existed:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if snapshot.kind == "symlink":
                        assert snapshot.link_target is not None
                        target.symlink_to(snapshot.link_target)
                    elif snapshot.kind == "directory":
                        assert snapshot.backup_path is not None
                        shutil.copytree(snapshot.backup_path, target, symlinks=True)
                    elif snapshot.kind == "file":
                        assert snapshot.backup_path is not None
                        shutil.copy2(snapshot.backup_path, target, follow_symlinks=False)
                restored.append(snapshot.relative_path)
            except OSError as exc:
                errors.append(f"{snapshot.relative_path}: {exc}")

        if errors:
            raise TransactionError("Transaction restore was incomplete: " + "; ".join(errors[:8]))
        return list(dict.fromkeys(restored))

    @staticmethod
    def _cleanup(transaction: WorkspaceTransaction | None) -> None:
        if transaction is None:
            return
        shutil.rmtree(transaction.backup_dir, ignore_errors=True)

    def finish(self, changed_files: list[str] | None = None) -> dict[str, object]:
        if self.current is None:
            return {"transaction_id": None, "undo_available": self.last is not None}

        transaction = self.current
        self.current = None
        transaction.changed_files = list(dict.fromkeys(changed_files or []))

        if not transaction.snapshots or not transaction.changed_files:
            self._cleanup(transaction)
            return {"transaction_id": transaction.transaction_id, "undo_available": self.last is not None}

        self._cleanup(self.last)
        self.last = transaction
        return {
            "transaction_id": transaction.transaction_id,
            "undo_available": True,
            "snapshot_count": len(transaction.snapshots),
            "backup_bytes": transaction.backup_bytes,
        }

    def rollback_current(self) -> list[str]:
        if self.current is None:
            return []
        transaction = self.current
        self.current = None
        try:
            return self._restore(transaction)
        finally:
            self._cleanup(transaction)

    def undo_last(self) -> tuple[str | None, list[str]]:
        if self.last is None:
            return None, []
        transaction = self.last
        self.last = None
        try:
            restored = self._restore(transaction)
            return transaction.transaction_id, restored
        finally:
            self._cleanup(transaction)

    def status(self) -> dict[str, object]:
        return {
            "active": self.current.transaction_id if self.current else None,
            "undo_transaction": self.last.transaction_id if self.last else None,
            "undo_files": list(self.last.changed_files) if self.last else [],
        }

    def close(self) -> None:
        self._cleanup(self.current)
        self._cleanup(self.last)
        self.current = None
        self.last = None
