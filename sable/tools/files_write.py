"""Workspace-confined filesystem mutation tools."""

from __future__ import annotations

import shutil

from ..config import contains_secret
from ..security import WorkspaceViolation
from .base import ToolResult


class WriteFileMixin:
    def write_file(self, path: str, content: str) -> ToolResult:
        target, denied = self._safe_path(path, "write_file")
        if denied:
            return denied
        assert target is not None
        if contains_secret(content):
            return ToolResult("write_file", False, error="Refused: content appears to contain a secret or token.")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
            if target.read_text(errors="replace") != content:
                return ToolResult("write_file", False, error=f"Write verification failed: {self._rel(target)}")
            return ToolResult("write_file", True, output=f"Written: {self._rel(target)} ({target.stat().st_size} bytes)", changed_files=[self._rel(target)])
        except OSError as exc:
            return ToolResult("write_file", False, error=str(exc))
    def append_file(self, path: str, content: str) -> ToolResult:
        target, denied = self._safe_path(path, "append_file")
        if denied:
            return denied
        assert target is not None
        if contains_secret(content):
            return ToolResult("append_file", False, error="Refused: appended content appears to contain a secret or token.")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a") as fh:
                fh.write(content)
            return ToolResult("append_file", True, output=f"Appended: {self._rel(target)}", changed_files=[self._rel(target)])
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
            updated = content.replace(old, new, 1)
            target.write_text(updated)
            if target.read_text(errors="replace") != updated:
                return ToolResult("patch_file", False, error=f"Patch verification failed: {self._rel(target)}")
            return ToolResult("patch_file", True, output=f"Patched: {self._rel(target)}", changed_files=[self._rel(target)])
        except OSError as exc:
            return ToolResult("patch_file", False, error=str(exc))
    def delete_file(self, path: str) -> ToolResult:
        target, denied = self._safe_path(path, "delete_file")
        if denied:
            return denied
        assert target is not None
        rel = self._rel(target)
        if target == self.workspace.root:
            return ToolResult("delete_file", False, error="Refused: workspace root cannot be deleted.", risk="blocked")
        try:
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            elif target.exists() or target.is_symlink():
                target.unlink()
            else:
                return ToolResult("delete_file", False, error=f"Not found: {rel}")
            return ToolResult("delete_file", True, output=f"Deleted: {rel}", changed_files=[rel], risk="high")
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
        try:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            if src_path.is_dir():
                shutil.copytree(src_path, dst_path, dirs_exist_ok=True)
            else:
                shutil.copy2(src_path, dst_path)
            return ToolResult("copy_file", True, output=f"Copied: {self._rel(src_path)} -> {self._rel(dst_path)}", changed_files=[self._rel(dst_path)])
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
        try:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src_path), str(dst_path))
            return ToolResult("move_file", True, output=f"Moved: {src_rel} -> {dst_rel}", changed_files=[src_rel, dst_rel])
        except OSError as exc:
            return ToolResult("move_file", False, error=str(exc))
    def make_dir(self, path: str) -> ToolResult:
        target, denied = self._safe_path(path, "make_dir")
        if denied:
            return denied
        assert target is not None
        try:
            target.mkdir(parents=True, exist_ok=True)
            return ToolResult("make_dir", True, output=f"Created directory: {self._rel(target)}", changed_files=[self._rel(target)])
        except OSError as exc:
            return ToolResult("make_dir", False, error=str(exc))
    def change_dir(self, path: str) -> ToolResult:
        try:
            target = self.workspace.chdir(path)
            return ToolResult("change_dir", True, output=f"Working directory: {target}")
        except (WorkspaceViolation, OSError) as exc:
            return ToolResult("change_dir", False, error=str(exc))
