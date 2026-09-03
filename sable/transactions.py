"""Persistent, bounded, conflict-aware workspace transactions.

The transaction manager is a deterministic recovery layer for Sable-owned file
mutations. It snapshots a path once, records the exact post-mutation fingerprint,
and restores only while the path still matches the state Sable last produced.
Git is optional metadata; rollback never depends on Git.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any

from .config import CONFIG_DIR, is_blocked_path, redact_secrets


class TransactionError(RuntimeError):
    """Raised when a transaction cannot preserve its safety guarantees."""


class TransactionStatus(str, Enum):
    OPEN = "OPEN"
    VERIFIED = "VERIFIED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"
    PARTIAL_ROLLBACK = "PARTIAL_ROLLBACK"
    ABORTED = "ABORTED"


class RollbackStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    ROLLED_BACK = "ROLLED_BACK"
    PARTIAL = "PARTIAL"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class PathSnapshot:
    relative_path: str
    existed: bool
    kind: str = "missing"
    backup_name: str | None = None
    link_target: str | None = None
    mode: int | None = None
    size: int = 0
    baseline_digest: str = "missing"
    post_digest: str | None = None
    conflict_sensitive: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PathSnapshot":
        allowed = cls.__dataclass_fields__
        return cls(**{key: value for key, value in data.items() if key in allowed})


@dataclass
class TransactionCheckpoint:
    checkpoint_id: str
    label: str
    created_at: str
    snapshots: list[PathSnapshot] = field(default_factory=list)
    backup_bytes: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TransactionCheckpoint":
        return cls(
            checkpoint_id=str(data.get("checkpoint_id", "")),
            label=str(data.get("label", "checkpoint")),
            created_at=str(data.get("created_at", "")),
            snapshots=[PathSnapshot.from_dict(item) for item in data.get("snapshots", [])],
            backup_bytes=int(data.get("backup_bytes", 0) or 0),
        )


@dataclass
class TaskTransaction:
    transaction_id: str
    task_summary: str
    storage_dir: str
    workspace_root: str
    started_at: str
    completed_at: str | None = None
    status: str = TransactionStatus.OPEN.value
    rollback_status: str = RollbackStatus.UNAVAILABLE.value
    repo_root: str | None = None
    git_head: str | None = None
    branch: str | None = None
    baseline_staged: list[str] = field(default_factory=list)
    baseline_unstaged: list[str] = field(default_factory=list)
    baseline_untracked: list[str] = field(default_factory=list)
    snapshots: list[PathSnapshot] = field(default_factory=list)
    touched_files: list[str] = field(default_factory=list)
    created_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)
    conflict_sensitive_files: list[str] = field(default_factory=list)
    verification: dict[str, Any] = field(default_factory=dict)
    checkpoints: list[TransactionCheckpoint] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    backup_bytes: int = 0
    rollback_outcome: dict[str, Any] = field(default_factory=dict)
    commit_sha: str | None = None

    @property
    def directory(self) -> Path:
        return Path(self.storage_dir)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskTransaction":
        fields = cls.__dataclass_fields__
        values = {key: value for key, value in data.items() if key in fields}
        values["snapshots"] = [PathSnapshot.from_dict(item) for item in data.get("snapshots", [])]
        values["checkpoints"] = [TransactionCheckpoint.from_dict(item) for item in data.get("checkpoints", [])]
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


WorkspaceTransaction = TaskTransaction


class WorkspaceTransactionManager:
    """Own transaction lifecycle, snapshots, checkpoints, history, and rollback."""

    def __init__(
        self,
        root: str | Path,
        *,
        storage_dir: str | Path | None = None,
        max_backup_bytes: int = 25 * 1024 * 1024,
        max_file_backup_bytes: int = 8 * 1024 * 1024,
        max_total_bytes: int = 100 * 1024 * 1024,
        max_entries: int = 4000,
        max_transactions: int = 10,
        max_checkpoints: int = 4,
    ):
        self.root = Path(root).expanduser().resolve()
        workspace_key = hashlib.sha256(str(self.root).encode("utf-8")).hexdigest()[:20]
        self._workspace_key = workspace_key
        self._default_storage = storage_dir is None
        self.storage_root = Path(storage_dir or (CONFIG_DIR / "transactions" / workspace_key)).expanduser()
        self.max_backup_bytes = max(1, int(max_backup_bytes))
        self.max_file_backup_bytes = max(1, int(max_file_backup_bytes))
        self.max_total_bytes = max(self.max_backup_bytes, int(max_total_bytes))
        self.max_entries = max(1, int(max_entries))
        self.max_transactions = max(1, int(max_transactions))
        self.max_checkpoints = max(0, int(max_checkpoints))
        self.current: TaskTransaction | None = None
        self.history: list[TaskTransaction] = []
        self._prepare_storage()
        self._load_history()

    @property
    def last(self) -> TaskTransaction | None:
        return self._latest_eligible()

    def _prepare_storage(self) -> None:
        try:
            self.storage_root.mkdir(parents=True, exist_ok=True)
        except OSError:
            if not self._default_storage:
                raise
            # Restricted hosts may not permit ~/.sable writes. Keep the same
            # persistent/bounded layout in Sable's private temporary control area.
            self.storage_root = Path(tempfile.gettempdir()) / "sable-transactions" / self._workspace_key
            self.storage_root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.storage_root, 0o700)
        except OSError:
            pass

    def _load_history(self) -> None:
        loaded: list[TaskTransaction] = []
        for metadata in self.storage_root.glob("*/metadata.json"):
            try:
                tx = TaskTransaction.from_dict(json.loads(metadata.read_text(encoding="utf-8")))
                if Path(tx.workspace_root).resolve() != self.root:
                    continue
                if tx.status == TransactionStatus.OPEN.value:
                    tx.status = TransactionStatus.ABORTED.value
                    tx.completed_at = tx.completed_at or _utc_now()
                    tx.rollback_status = RollbackStatus.AVAILABLE.value if tx.snapshots else RollbackStatus.UNAVAILABLE.value
                    self._save(tx)
                loaded.append(tx)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        self.history = sorted(loaded, key=lambda tx: (tx.started_at, tx.transaction_id))
        self._prune()

    @staticmethod
    def _safe_summary(label: str) -> str:
        return redact_secrets(" ".join(str(label or "task").split()))[:240]

    def begin(self, label: str = "task") -> str:
        if self.current is not None:
            raise TransactionError("A workspace transaction is already active.")
        txid = f"{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{uuid.uuid4().hex[:8]}"
        directory = self.storage_root / txid
        directory.mkdir(parents=True, exist_ok=False)
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass
        git = self._git_baseline()
        self.current = TaskTransaction(
            transaction_id=txid,
            task_summary=self._safe_summary(label),
            storage_dir=str(directory), workspace_root=str(self.root), started_at=_utc_now(),
            repo_root=git["repo_root"], git_head=git["head"], branch=git["branch"],
            baseline_staged=git["staged"], baseline_unstaged=git["unstaged"],
            baseline_untracked=git["untracked"],
        )
        self._save(self.current)
        return txid

    def _git_run(self, args: list[str], cwd: Path | None = None) -> str | None:
        try:
            proc = subprocess.run(
                ["git", *args], cwd=str(cwd or self.root), capture_output=True,
                text=True, timeout=10, shell=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return proc.stdout if proc.returncode == 0 else None

    def _git_paths(self, args: list[str], repo_root: Path) -> list[str]:
        output = self._git_run(args, repo_root)
        if output is None:
            return []
        paths: list[str] = []
        for raw in output.split("\0"):
            if not raw:
                continue
            try:
                paths.append((repo_root / raw).resolve(strict=False).relative_to(self.root).as_posix())
            except ValueError:
                continue
        return sorted(set(paths))

    def _git_baseline(self) -> dict[str, Any]:
        empty = {"repo_root": None, "head": None, "branch": None, "staged": [], "unstaged": [], "untracked": []}
        raw_root = self._git_run(["rev-parse", "--show-toplevel"])
        if not raw_root:
            return empty
        try:
            repo_root = Path(raw_root.strip()).resolve()
            repo_root.relative_to(self.root)
        except (OSError, ValueError):
            return empty
        head = self._git_run(["rev-parse", "HEAD"], repo_root)
        branch = self._git_run(["branch", "--show-current"], repo_root)
        return {
            "repo_root": str(repo_root), "head": head.strip() if head else None,
            "branch": branch.strip() if branch and branch.strip() else None,
            "staged": self._git_paths(["diff", "--cached", "--name-only", "-z", "--no-ext-diff"], repo_root),
            "unstaged": self._git_paths(["diff", "--name-only", "-z", "--no-ext-diff"], repo_root),
            "untracked": self._git_paths(["ls-files", "--others", "--exclude-standard", "-z"], repo_root),
        }

    @staticmethod
    def _paths_overlap(left: str, right: str) -> bool:
        return left == right or left.startswith(right.rstrip("/") + "/") or right.startswith(left.rstrip("/") + "/")

    def _is_conflict_sensitive(self, rel: str) -> bool:
        if self.current is None:
            return False
        dirty = self.current.baseline_staged + self.current.baseline_unstaged + self.current.baseline_untracked
        return any(self._paths_overlap(rel, item) for item in dirty)

    def _relative(self, target: Path) -> str:
        resolved = target.resolve(strict=False)
        try:
            rel = resolved.relative_to(self.root).as_posix()
        except ValueError as exc:
            raise TransactionError(f"Transaction target escapes workspace: {target}") from exc
        if not rel or rel == "." or is_blocked_path(rel):
            raise TransactionError(f"Transaction target is protected or too broad: {rel or '.'}")
        return rel

    def _target(self, relative_path: str) -> Path:
        pure = PurePosixPath(relative_path)
        if pure.is_absolute() or ".." in pure.parts or not pure.parts or is_blocked_path(relative_path):
            raise TransactionError(f"Unsafe transaction path: {relative_path}")
        target = self.root.joinpath(*pure.parts)
        try:
            target.parent.resolve(strict=False).relative_to(self.root)
        except ValueError as exc:
            raise TransactionError(f"Transaction parent escapes workspace: {relative_path}") from exc
        return target

    def _inspect_tree(self, target: Path) -> tuple[int, int]:
        if target.is_symlink():
            return 0, 1
        if target.is_file():
            size = target.stat().st_size
            if size > self.max_file_backup_bytes:
                raise TransactionError(f"File snapshot exceeds {self.max_file_backup_bytes // (1024 * 1024)} MiB: {target.name}")
            return size, 1
        if not target.is_dir():
            return 0, 1
        total, entries = 0, 1
        for base, dirs, files in os.walk(target, followlinks=False):
            base_path = Path(base)
            for name in list(dirs) + list(files):
                path = base_path / name
                entries += 1
                if entries > self.max_entries:
                    raise TransactionError(f"Transaction snapshot exceeds {self.max_entries} filesystem entries.")
                try:
                    rel = path.relative_to(self.root).as_posix()
                except ValueError as exc:
                    raise TransactionError(f"Snapshot entry escapes workspace: {path}") from exc
                if is_blocked_path(rel):
                    raise TransactionError(f"Refused to snapshot protected path inside mutation target: {rel}")
                if path.is_symlink():
                    try:
                        resolved_rel = path.resolve(strict=False).relative_to(self.root).as_posix()
                    except ValueError as exc:
                        raise TransactionError(f"Snapshot symlink escapes workspace: {rel}") from exc
                    if is_blocked_path(resolved_rel):
                        raise TransactionError(f"Snapshot symlink resolves to protected path: {rel}")
                elif path.is_file():
                    size = path.stat().st_size
                    if size > self.max_file_backup_bytes:
                        raise TransactionError(f"File snapshot exceeds {self.max_file_backup_bytes // (1024 * 1024)} MiB: {rel}")
                    total += size
                    if total > self.max_backup_bytes:
                        raise TransactionError(f"Transaction snapshot exceeds {self.max_backup_bytes // (1024 * 1024)} MiB.")
        return total, entries

    @staticmethod
    def _fingerprint(target: Path) -> str:
        digest = hashlib.sha256()
        if target.is_symlink():
            digest.update(b"symlink\0" + os.readlink(target).encode("utf-8", errors="surrogateescape"))
            return digest.hexdigest()
        if not target.exists():
            return "missing"
        if target.is_file():
            digest.update(b"file\0")
            with target.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()
        digest.update(b"directory\0")
        for base, dirs, files in os.walk(target, followlinks=False):
            dirs.sort(); files.sort()
            base_path = Path(base)
            for name in dirs + files:
                path = base_path / name
                digest.update(path.relative_to(target).as_posix().encode("utf-8", errors="surrogateescape") + b"\0")
                if path.is_symlink():
                    digest.update(b"link\0" + os.readlink(path).encode("utf-8", errors="surrogateescape"))
                elif path.is_file():
                    digest.update(b"file\0")
                    with path.open("rb") as fh:
                        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                            digest.update(chunk)
                else:
                    digest.update(b"dir\0")
        return digest.hexdigest()

    def _snapshot_path(self, target: Path, rel: str, backup_name: str) -> PathSnapshot:
        existed = target.exists() or target.is_symlink()
        snapshot = PathSnapshot(
            relative_path=rel, existed=existed,
            conflict_sensitive=self._is_conflict_sensitive(rel),
            baseline_digest=self._fingerprint(target),
        )
        if not existed:
            return snapshot
        size, _ = self._inspect_tree(target)
        if self.current and self.current.backup_bytes + size > self.max_backup_bytes:
            raise TransactionError(f"Refused transactional mutation: backup budget would exceed {self.max_backup_bytes // (1024 * 1024)} MiB.")
        slot = self.current.directory / backup_name if self.current else self.storage_root / backup_name
        slot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.mode = stat.S_IMODE(target.lstat().st_mode)
        snapshot.size = size
        snapshot.backup_name = backup_name
        if target.is_symlink():
            snapshot.kind = "symlink"; snapshot.link_target = os.readlink(target)
        elif target.is_dir():
            snapshot.kind = "directory"; shutil.copytree(target, slot, symlinks=True)
        else:
            snapshot.kind = "file"; shutil.copy2(target, slot, follow_symlinks=False)
        return snapshot

    def capture(self, target: str | Path) -> None:
        if self.current is None:
            return
        path = Path(target).expanduser().resolve(strict=False)
        rel = self._relative(path)
        if any(s.relative_path == rel or rel.startswith(s.relative_path.rstrip("/") + "/") for s in self.current.snapshots):
            return
        snapshot = self._snapshot_path(path, rel, f"snapshots/{len(self.current.snapshots):04d}")
        self.current.snapshots.append(snapshot)
        self.current.backup_bytes += snapshot.size
        if snapshot.conflict_sensitive and rel not in self.current.conflict_sensitive_files:
            self.current.conflict_sensitive_files.append(rel)
        self._save(self.current)

    def record_mutation(self, paths: list[str]) -> None:
        if self.current is None:
            return
        for raw in paths:
            rel = PurePosixPath(raw).as_posix()
            if rel not in self.current.touched_files:
                self.current.touched_files.append(rel)
            for snapshot in self.current.snapshots:
                if self._paths_overlap(snapshot.relative_path, rel):
                    snapshot.post_digest = self._fingerprint(self._target(snapshot.relative_path))
        self._save(self.current)

    def record_action(self, action: str, *, risk: str = "normal", capability: str = "", approval_required: bool = False, success: bool | None = None) -> None:
        if self.current is None:
            return
        self.current.actions.append({
            "action": str(action)[:80], "risk": str(risk)[:20],
            "requested_capability": str(capability or action)[:80],
            "approval_required": bool(approval_required), "success": success, "timestamp": _utc_now(),
        })
        self.current.actions = self.current.actions[-200:]
        self._save(self.current)

    def checkpoint(self, label: str = "checkpoint") -> str | None:
        if self.current is None or not self.current.snapshots or self.max_checkpoints == 0:
            return None
        if len(self.current.checkpoints) >= self.max_checkpoints:
            return None
        checkpoint_id = f"cp{len(self.current.checkpoints) + 1}"
        checkpoint = TransactionCheckpoint(checkpoint_id, self._safe_summary(label), _utc_now())
        for index, baseline in enumerate(self.current.snapshots):
            target = self._target(baseline.relative_path)
            snapshot = self._snapshot_path(target, baseline.relative_path, f"checkpoints/{checkpoint_id}/{index:04d}")
            checkpoint.snapshots.append(snapshot)
            checkpoint.backup_bytes += snapshot.size
            self.current.backup_bytes += snapshot.size
        self.current.checkpoints.append(checkpoint)
        self._save(self.current)
        return checkpoint_id

    @staticmethod
    def _remove_current(target: Path) -> None:
        if target.is_symlink() or target.is_file():
            target.unlink(missing_ok=True)
        elif target.is_dir():
            shutil.rmtree(target)

    def _restore_snapshot(self, transaction: TaskTransaction, snapshot: PathSnapshot) -> None:
        target = self._target(snapshot.relative_path)
        self._remove_current(target)
        if not snapshot.existed:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        backup = transaction.directory / snapshot.backup_name if snapshot.backup_name else None
        if snapshot.kind == "symlink":
            if snapshot.link_target is None:
                raise TransactionError(f"Missing symlink target for {snapshot.relative_path}")
            target.symlink_to(snapshot.link_target)
        elif snapshot.kind == "directory":
            if backup is None or not backup.is_dir():
                raise TransactionError(f"Missing directory snapshot for {snapshot.relative_path}")
            shutil.copytree(backup, target, symlinks=True)
        elif snapshot.kind == "file":
            if backup is None or not backup.is_file():
                raise TransactionError(f"Missing file snapshot for {snapshot.relative_path}")
            shutil.copy2(backup, target, follow_symlinks=False)
        if snapshot.mode is not None and not target.is_symlink():
            try:
                os.chmod(target, snapshot.mode)
            except OSError:
                pass

    def _rollback_plan(self, snapshots: list[PathSnapshot]) -> dict[str, list[str]]:
        plan = {"restorable": [], "already_restored": [], "conflicts": []}
        for snapshot in snapshots:
            current = self._fingerprint(self._target(snapshot.relative_path))
            if current == snapshot.baseline_digest:
                plan["already_restored"].append(snapshot.relative_path)
            elif snapshot.post_digest is not None and current == snapshot.post_digest:
                plan["restorable"].append(snapshot.relative_path)
            else:
                plan["conflicts"].append(snapshot.relative_path)
        return plan

    def _rollback(self, transaction: TaskTransaction, *, dry_run: bool = False) -> dict[str, Any]:
        snapshots = list(reversed(transaction.snapshots))
        plan = self._rollback_plan(snapshots)
        outcome: dict[str, Any] = {
            "transaction_id": transaction.transaction_id, "dry_run": dry_run,
            "restored": [], "removed_created": [], "recreated_deleted": [],
            "already_restored": plan["already_restored"], "conflicts": plan["conflicts"], "errors": [],
        }
        if dry_run:
            outcome["would_restore"] = plan["restorable"]
            return outcome
        restorable = set(plan["restorable"])
        for snapshot in snapshots:
            if snapshot.relative_path not in restorable:
                continue
            try:
                self._restore_snapshot(transaction, snapshot)
                outcome["restored"].append(snapshot.relative_path)
                if snapshot.existed and snapshot.post_digest == "missing":
                    outcome["recreated_deleted"].append(snapshot.relative_path)
                elif not snapshot.existed:
                    outcome["removed_created"].append(snapshot.relative_path)
            except (OSError, TransactionError) as exc:
                outcome["errors"].append(f"{snapshot.relative_path}: {exc}")
        complete = not outcome["conflicts"] and not outcome["errors"]
        transaction.completed_at = _utc_now()
        transaction.rollback_outcome = outcome
        transaction.status = TransactionStatus.ROLLED_BACK.value if complete else TransactionStatus.PARTIAL_ROLLBACK.value
        transaction.rollback_status = RollbackStatus.ROLLED_BACK.value if complete else RollbackStatus.PARTIAL.value
        self._save(transaction)
        return outcome

    def restore_checkpoint(self, checkpoint_id: str | None = None) -> dict[str, Any]:
        if self.current is None or not self.current.checkpoints:
            raise TransactionError("No transaction checkpoint is available.")
        checkpoint = next((item for item in reversed(self.current.checkpoints) if checkpoint_id is None or item.checkpoint_id == checkpoint_id), None)
        if checkpoint is None:
            raise TransactionError(f"Unknown checkpoint: {checkpoint_id}")
        plan = self._rollback_plan(list(reversed(self.current.snapshots)))
        if plan["conflicts"]:
            raise TransactionError("Checkpoint restore refused because files changed outside Sable: " + ", ".join(plan["conflicts"][:8]))
        by_path = {item.relative_path: item for item in checkpoint.snapshots}
        restored: list[str] = []
        for rel in reversed([item.relative_path for item in self.current.snapshots]):
            snapshot = by_path.get(rel)
            if snapshot:
                self._restore_snapshot(self.current, snapshot)
                restored.append(rel)
        self.record_mutation(restored)
        return {"checkpoint_id": checkpoint.checkpoint_id, "restored": restored}

    @staticmethod
    def _verification_summary(verification: dict[str, Any] | None) -> dict[str, Any]:
        if not verification:
            return {}
        checks = []
        for check in verification.get("checks", []):
            result = getattr(check, "result", None)
            checks.append({"name": str(getattr(check, "name", "check")), "success": bool(getattr(result, "success", False))})
        return {
            "status": str(verification.get("status", "unknown")),
            "summary": redact_secrets(str(verification.get("summary", "")))[:500], "checks": checks,
        }

    def finish(self, changed_files: list[str] | None = None, *, status: str = TransactionStatus.COMPLETED.value, verification: dict[str, Any] | None = None, commit_sha: str | None = None) -> dict[str, object]:
        if self.current is None:
            return {"transaction_id": None, "undo_available": self.last is not None}
        transaction = self.current
        self.current = None
        for raw in changed_files or []:
            rel = PurePosixPath(raw).as_posix()
            if rel not in transaction.touched_files:
                transaction.touched_files.append(rel)
        for snapshot in transaction.snapshots:
            snapshot.post_digest = self._fingerprint(self._target(snapshot.relative_path))
            if snapshot.post_digest == snapshot.baseline_digest:
                continue
            if not snapshot.existed and snapshot.post_digest != "missing":
                transaction.created_files.append(snapshot.relative_path)
            elif snapshot.existed and snapshot.post_digest == "missing":
                transaction.deleted_files.append(snapshot.relative_path)
            else:
                transaction.modified_files.append(snapshot.relative_path)
        transaction.touched_files = list(dict.fromkeys(transaction.touched_files))
        transaction.created_files = list(dict.fromkeys(transaction.created_files))
        transaction.modified_files = list(dict.fromkeys(transaction.modified_files))
        transaction.deleted_files = list(dict.fromkeys(transaction.deleted_files))
        transaction.verification = self._verification_summary(verification)
        transaction.commit_sha = commit_sha or transaction.commit_sha
        transaction.completed_at = _utc_now()
        transaction.status = status if status in {item.value for item in TransactionStatus} else TransactionStatus.COMPLETED.value
        actual_changes = transaction.created_files + transaction.modified_files + transaction.deleted_files
        transaction.rollback_status = RollbackStatus.AVAILABLE.value if transaction.snapshots and actual_changes else RollbackStatus.UNAVAILABLE.value
        self._save(transaction)
        self.history.append(transaction)
        self.history.sort(key=lambda tx: (tx.started_at, tx.transaction_id))
        self._prune()
        return {
            "transaction_id": transaction.transaction_id,
            "undo_available": transaction.rollback_status == RollbackStatus.AVAILABLE.value,
            "snapshot_count": len(transaction.snapshots), "backup_bytes": transaction.backup_bytes,
            "status": transaction.status, "conflict_sensitive_files": list(transaction.conflict_sensitive_files),
        }

    def rollback_current(self) -> dict[str, Any]:
        if self.current is None:
            return {"transaction_id": None, "restored": [], "conflicts": [], "errors": []}
        transaction = self.current
        self.current = None
        outcome = self._rollback(transaction)
        if all(item.transaction_id != transaction.transaction_id for item in self.history):
            self.history.append(transaction)
        self._prune()
        return outcome

    def _latest_eligible(self) -> TaskTransaction | None:
        for transaction in reversed(self.history):
            if transaction.rollback_status in {RollbackStatus.AVAILABLE.value, RollbackStatus.PARTIAL.value}:
                return transaction
        return None

    def get(self, transaction_id: str) -> TaskTransaction | None:
        if self.current and self.current.transaction_id == transaction_id:
            return self.current
        exact = [tx for tx in self.history if tx.transaction_id == transaction_id]
        if exact:
            return exact[-1]
        prefix = [tx for tx in self.history if tx.transaction_id.startswith(transaction_id)]
        return prefix[0] if len(prefix) == 1 else None

    def undo(self, transaction_id: str | None = None, *, dry_run: bool = False) -> dict[str, Any]:
        transaction = self.get(transaction_id) if transaction_id else self._latest_eligible()
        if transaction is None:
            raise TransactionError("No reversible Sable transaction is available.")
        if transaction.rollback_status not in {RollbackStatus.AVAILABLE.value, RollbackStatus.PARTIAL.value}:
            raise TransactionError(f"Transaction {transaction.transaction_id} is not rollback-eligible.")
        outcome = self._rollback(transaction, dry_run=dry_run)
        if not dry_run:
            self._prune()
        return outcome

    def undo_last(self) -> tuple[str | None, list[str]]:
        try:
            outcome = self.undo()
        except TransactionError:
            return None, []
        return str(outcome["transaction_id"]), list(outcome["restored"])

    def set_verification(self, verification: dict[str, Any]) -> None:
        if self.current:
            self.current.verification = self._verification_summary(verification)
            if verification.get("status") == "pass":
                self.current.status = TransactionStatus.VERIFIED.value
            self._save(self.current)

    def set_commit(self, sha: str) -> None:
        if self.current:
            self.current.commit_sha = str(sha).strip() or None
            self._save(self.current)

    def auto_commit_conflicts(self) -> list[str]:
        return list(self.current.conflict_sensitive_files) if self.current else []

    def list_transactions(self, limit: int = 10) -> list[dict[str, Any]]:
        items = ([self.current] if self.current else []) + list(reversed(self.history))
        return [self._public(tx) for tx in items[:max(1, min(50, int(limit)))]]

    def status(self, transaction_id: str | None = None) -> dict[str, object]:
        if transaction_id:
            transaction = self.get(transaction_id)
            if transaction is None:
                raise TransactionError(f"Unknown transaction: {transaction_id}")
            return self._public(transaction, detail=True)
        latest = self.history[-1] if self.history else None
        undo = self._latest_eligible()
        return {
            "active": self._public(self.current) if self.current else None,
            "latest": self._public(latest) if latest else None,
            "undo_transaction": undo.transaction_id if undo else None,
            "retained_transactions": len(self.history),
        }

    @staticmethod
    def _public(transaction: TaskTransaction, detail: bool = False) -> dict[str, Any]:
        changed = list(dict.fromkeys(transaction.created_files + transaction.modified_files + transaction.deleted_files or transaction.touched_files))
        data: dict[str, Any] = {
            "transaction_id": transaction.transaction_id, "task_summary": transaction.task_summary,
            "status": transaction.status, "started_at": transaction.started_at,
            "completed_at": transaction.completed_at, "changed_files": changed,
            "verification": transaction.verification, "rollback_status": transaction.rollback_status,
            "conflict_sensitive_files": list(transaction.conflict_sensitive_files),
            "checkpoint_count": len(transaction.checkpoints), "commit_sha": transaction.commit_sha,
        }
        if detail:
            data.update({
                "created_files": list(transaction.created_files), "modified_files": list(transaction.modified_files),
                "deleted_files": list(transaction.deleted_files), "git_head": transaction.git_head,
                "branch": transaction.branch, "baseline_staged": list(transaction.baseline_staged),
                "baseline_unstaged": list(transaction.baseline_unstaged), "baseline_untracked": list(transaction.baseline_untracked),
                "checkpoints": [{"checkpoint_id": item.checkpoint_id, "label": item.label, "created_at": item.created_at} for item in transaction.checkpoints],
                "rollback_outcome": transaction.rollback_outcome, "backup_bytes": transaction.backup_bytes,
            })
        return data

    def _save(self, transaction: TaskTransaction) -> None:
        transaction.directory.mkdir(parents=True, exist_ok=True)
        metadata = transaction.directory / "metadata.json"
        fd, tmp_name = tempfile.mkstemp(prefix=".metadata-", suffix=".json", dir=str(transaction.directory), text=True)
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(transaction.to_dict(), fh, indent=2, sort_keys=True)
                fh.write("\n"); fh.flush(); os.fsync(fh.fileno())
            os.replace(tmp, metadata)
            try:
                os.chmod(metadata, 0o600)
            except OSError:
                pass
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    @staticmethod
    def _directory_size(path: Path) -> int:
        total = 0
        try:
            for item in path.rglob("*"):
                if item.is_file() and not item.is_symlink():
                    total += item.stat().st_size
        except OSError:
            pass
        return total

    def _prune(self) -> None:
        self.history.sort(key=lambda tx: (tx.started_at, tx.transaction_id))
        while len(self.history) > self.max_transactions:
            victim = self.history.pop(0)
            shutil.rmtree(victim.directory, ignore_errors=True)
        total = sum(self._directory_size(tx.directory) for tx in self.history)
        while self.history and total > self.max_total_bytes:
            victim = self.history.pop(0)
            total -= self._directory_size(victim.directory)
            shutil.rmtree(victim.directory, ignore_errors=True)

    def close(self) -> None:
        if self.current is not None:
            self.current.status = TransactionStatus.ABORTED.value
            self.current.completed_at = _utc_now()
            self.current.rollback_status = RollbackStatus.AVAILABLE.value if self.current.snapshots else RollbackStatus.UNAVAILABLE.value
            self._save(self.current)
            self.history.append(self.current)
            self.current = None
        self._prune()
