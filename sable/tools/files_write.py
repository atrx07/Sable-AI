"""Workspace-confined filesystem mutation tools."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from ..config import contains_secret
from ..patches import PatchError, apply_file_patch, parse_unified_diff
from ..security import WorkspaceViolation
from .base import ToolResult


class WriteFileMixin:
    @staticmethod
    def _atomic_write_bytes(target: Path, content: bytes, mode: int | None = None) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.sable-", dir=str(target.parent))
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            if mode is not None:
                try:
                    os.chmod(tmp, mode)
                except OSError:
                    pass
            os.replace(tmp, target)
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    @staticmethod
    def _atomic_write_text(target: Path, content: str) -> None:
        """Atomically replace a text file using a temp file in the same directory."""
        target.parent.mkdir(parents=True, exist_ok=True)
        previous_mode = None
        try:
            if target.exists():
                previous_mode = target.stat().st_mode & 0o777
        except OSError:
            previous_mode = None

        fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.sable-", dir=str(target.parent), text=True)
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            if previous_mode is not None:
                try:
                    os.chmod(tmp, previous_mode)
                except OSError:
                    pass
            os.replace(tmp, target)
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def write_file(self, path: str, content: str) -> ToolResult:
        target, denied = self._safe_path(path, "write_file")
        if denied:
            return denied
        assert target is not None
        if contains_secret(content):
            return ToolResult("write_file", False, error="Refused: content appears to contain a secret or token.")
        transaction_denied = self._capture_before_mutation(target, "write_file")
        if transaction_denied:
            return transaction_denied
        try:
            self._atomic_write_text(target, content)
            if target.read_text(errors="replace") != content:
                return ToolResult("write_file", False, error=f"Write verification failed: {self._rel(target)}")
            changed = [self._rel(target)]
            self._record_mutation(changed)
            return ToolResult("write_file", True, output=f"Written atomically: {self._rel(target)} ({target.stat().st_size} bytes)", changed_files=changed)
        except OSError as exc:
            return ToolResult("write_file", False, error=str(exc))

    def append_file(self, path: str, content: str) -> ToolResult:
        target, denied = self._safe_path(path, "append_file")
        if denied:
            return denied
        assert target is not None
        if contains_secret(content):
            return ToolResult("append_file", False, error="Refused: appended content appears to contain a secret or token.")
        transaction_denied = self._capture_before_mutation(target, "append_file")
        if transaction_denied:
            return transaction_denied
        try:
            original = target.read_text(errors="strict") if target.exists() else ""
            updated = original + content
            self._atomic_write_text(target, updated)
            if target.read_text(errors="replace") != updated:
                return ToolResult("append_file", False, error=f"Append verification failed: {self._rel(target)}")
            changed = [self._rel(target)]
            self._record_mutation(changed)
            return ToolResult("append_file", True, output=f"Appended: {self._rel(target)}", changed_files=changed)
        except OSError as exc:
            return ToolResult("append_file", False, error=str(exc))

    def patch_file(self, path: str, old: str, new: str) -> ToolResult:
        target, denied = self._safe_path(path, "patch_file")
        if denied:
            return denied
        assert target is not None
        if contains_secret(new):
            return ToolResult("patch_file", False, error="Refused: replacement text appears to contain a secret or token.")
        try:
            content = target.read_text(errors="replace")
            count = content.count(old)
            if count == 0:
                return ToolResult("patch_file", False, error=f"Text not found in {self._rel(target)}")
            if count > 1:
                return ToolResult("patch_file", False, error=f"Patch is ambiguous: old text occurs {count} times in {self._rel(target)}")
            transaction_denied = self._capture_before_mutation(target, "patch_file")
            if transaction_denied:
                return transaction_denied
            updated = content.replace(old, new, 1)
            self._atomic_write_text(target, updated)
            if target.read_text(errors="replace") != updated:
                return ToolResult("patch_file", False, error=f"Patch verification failed: {self._rel(target)}")
            changed = [self._rel(target)]
            self._record_mutation(changed)
            return ToolResult("patch_file", True, output=f"Patched atomically: {self._rel(target)}", changed_files=changed)
        except OSError as exc:
            return ToolResult("patch_file", False, error=str(exc))

    def apply_patch(self, patch: str) -> ToolResult:
        """Apply a unified diff as an all-or-nothing workspace operation."""
        try:
            parsed = parse_unified_diff(patch)
        except PatchError as exc:
            return ToolResult("apply_patch", False, error=f"Invalid patch: {exc}")

        prepared: list[dict[str, object]] = []
        try:
            for file_patch in parsed:
                target, denied = self._safe_path(file_patch.path, "apply_patch")
                if denied:
                    return denied
                assert target is not None
                if target.exists() and not target.is_file():
                    return ToolResult("apply_patch", False, error=f"Patch target is not a regular file: {file_patch.path}")
                existed = target.exists()
                original_bytes = target.read_bytes() if existed else None
                try:
                    # Text-mode reading normalizes platform newlines so a portable
                    # unified diff matches the same file on Windows and POSIX.
                    original = target.read_text(encoding="utf-8") if original_bytes is not None else None
                except UnicodeDecodeError:
                    return ToolResult("apply_patch", False, error=f"Patch target is not UTF-8 text: {file_patch.path}")
                updated = apply_file_patch(file_patch, original)
                if updated is not None and contains_secret(updated):
                    return ToolResult("apply_patch", False, error=f"Refused: patched content appears to contain a secret: {file_patch.path}")
                prepared.append({
                    "target": target,
                    "relative": self._rel(target),
                    "existed": existed,
                    "original": original_bytes,
                    "mode": (target.stat().st_mode & 0o777) if existed else None,
                    "updated": updated,
                })
        except (OSError, PatchError) as exc:
            return ToolResult("apply_patch", False, error=str(exc))

        for item in prepared:
            denied = self._capture_before_mutation(item["target"], "apply_patch")
            if denied:
                return denied

        applied: list[dict[str, object]] = []
        try:
            for item in prepared:
                target = item["target"]
                assert isinstance(target, Path)
                updated = item["updated"]
                applied.append(item)
                if updated is None:
                    target.unlink()
                else:
                    self._atomic_write_text(target, str(updated))
                    if target.read_text(encoding="utf-8") != updated:
                        raise OSError(f"Patch verification failed: {item['relative']}")
        except (OSError, ValueError) as exc:
            rollback_errors: list[str] = []
            for item in reversed(applied):
                target = item["target"]
                assert isinstance(target, Path)
                try:
                    if item["existed"]:
                        original = item["original"]
                        assert isinstance(original, bytes)
                        self._atomic_write_bytes(target, original, item["mode"])
                    else:
                        target.unlink(missing_ok=True)
                except OSError as rollback_exc:
                    rollback_errors.append(f"{item['relative']}: {rollback_exc}")
            detail = f"Patch application failed and applied files were restored: {exc}"
            if rollback_errors:
                detail += "; rollback errors: " + "; ".join(rollback_errors)
            return ToolResult("apply_patch", False, error=detail, risk="blocked" if rollback_errors else "normal")

        changed = [str(item["relative"]) for item in prepared]
        self._record_mutation(changed)
        return ToolResult(
            "apply_patch",
            True,
            output=f"Applied validated patch atomically to {len(changed)} file(s): {', '.join(changed)}",
            changed_files=changed,
        )

    def delete_file(self, path: str) -> ToolResult:
        target, denied = self._safe_path(path, "delete_file")
        if denied:
            return denied
        assert target is not None
        rel = self._rel(target)
        if target == self.workspace.root:
            return ToolResult("delete_file", False, error="Refused: workspace root cannot be deleted.", risk="blocked")
        transaction_denied = self._capture_before_mutation(target, "delete_file")
        if transaction_denied:
            return transaction_denied
        try:
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            elif target.exists() or target.is_symlink():
                target.unlink()
            else:
                return ToolResult("delete_file", False, error=f"Not found: {rel}")
            changed = [rel]
            self._record_mutation(changed)
            return ToolResult("delete_file", True, output=f"Deleted: {rel}", changed_files=changed, risk="high")
        except OSError as exc:
            return ToolResult("delete_file", False, error=str(exc), risk="high")

    def copy_file(self, src: str, dst: str) -> ToolResult:
        src_path, denied = self._safe_path(src, "copy_file")
        if denied:
            return denied
        dst_path, denied = self._safe_path(dst, "copy_file")
        if denied:
            return denied
        assert src_path is not None and dst_path is not None
        if src_path.is_dir():
            entries = 0
            for base, dirs, files in os.walk(src_path, followlinks=False):
                for name in list(dirs) + list(files):
                    entries += 1
                    if entries > self.transactions.max_entries:
                        return ToolResult("copy_file", False, error="Copy source exceeds the transaction entry limit.")
                    _, nested_denied = self._safe_path(str(Path(base) / name), "copy_file")
                    if nested_denied:
                        return nested_denied
        transaction_denied = self._capture_before_mutation(dst_path, "copy_file")
        if transaction_denied:
            return transaction_denied
        try:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            if src_path.is_dir():
                shutil.copytree(src_path, dst_path, dirs_exist_ok=True, symlinks=True)
            else:
                shutil.copy2(src_path, dst_path)
            changed = [self._rel(dst_path)]
            self._record_mutation(changed)
            return ToolResult("copy_file", True, output=f"Copied: {self._rel(src_path)} -> {self._rel(dst_path)}", changed_files=changed)
        except OSError as exc:
            return ToolResult("copy_file", False, error=str(exc))

    def move_file(self, src: str, dst: str) -> ToolResult:
        src_path, denied = self._safe_path(src, "move_file")
        if denied:
            return denied
        dst_path, denied = self._safe_path(dst, "move_file")
        if denied:
            return denied
        assert src_path is not None and dst_path is not None
        src_rel, dst_rel = self._rel(src_path), self._rel(dst_path)
        transaction_denied = self._capture_before_mutation(src_path, "move_file")
        if transaction_denied:
            return transaction_denied
        transaction_denied = self._capture_before_mutation(dst_path, "move_file")
        if transaction_denied:
            return transaction_denied
        try:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src_path), str(dst_path))
            changed = [src_rel, dst_rel]
            self._record_mutation(changed)
            return ToolResult("move_file", True, output=f"Moved: {src_rel} -> {dst_rel}", changed_files=changed)
        except OSError as exc:
            return ToolResult("move_file", False, error=str(exc))

    def make_dir(self, path: str) -> ToolResult:
        target, denied = self._safe_path(path, "make_dir")
        if denied:
            return denied
        assert target is not None
        transaction_denied = self._capture_before_mutation(target, "make_dir")
        if transaction_denied:
            return transaction_denied
        try:
            target.mkdir(parents=True, exist_ok=True)
            changed = [self._rel(target)]
            self._record_mutation(changed)
            return ToolResult("make_dir", True, output=f"Created directory: {self._rel(target)}", changed_files=changed)
        except OSError as exc:
            return ToolResult("make_dir", False, error=str(exc))

    def change_dir(self, path: str) -> ToolResult:
        try:
            target = self.workspace.chdir(path)
            return ToolResult("change_dir", True, output=f"Working directory: {target}")
        except (WorkspaceViolation, OSError) as exc:
            return ToolResult("change_dir", False, error=str(exc))
