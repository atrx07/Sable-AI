import tempfile
import unittest
from pathlib import Path

from sable.tools import ToolExecutor


class ToolExecutorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.executor = ToolExecutor(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_write_read_patch(self):
        write = self.executor.write_file("a.txt", "hello")
        self.assertTrue(write.success)
        self.assertEqual(write.changed_files, ["a.txt"])

        read = self.executor.read_file("a.txt")
        self.assertTrue(read.success)
        self.assertIn("hello", read.output)

        patch = self.executor.patch_file("a.txt", "hello", "world")
        self.assertTrue(patch.success)
        self.assertEqual(Path(self.tmp.name, "a.txt").read_text(), "world")

    def test_ambiguous_patch_is_refused(self):
        Path(self.tmp.name, "a.txt").write_text("x x")
        result = self.executor.patch_file("a.txt", "x", "y")
        self.assertFalse(result.success)
        self.assertIn("ambiguous", result.error.lower())

    def test_env_file_is_blocked(self):
        Path(self.tmp.name, ".env").write_text("TOKEN=secret")
        result = self.executor.read_file(".env")
        self.assertFalse(result.success)
        self.assertIn("protected", result.error.lower())

    def test_dispatch_enforces_mode(self):
        result = self.executor.dispatch("write_file", {"path": "x.txt", "content": "x"}, mode="plan")
        self.assertFalse(result.success)
        self.assertTrue(result.approval_required)

    def test_command_uses_argv_without_shell(self):
        result = self.executor.dispatch("run_command", {"argv": ["python", "--version"]}, mode="build")
        self.assertTrue(result.success)

class SymlinkSecretTests(unittest.TestCase):
    def test_copy_directory_with_protected_descendant_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp, "source")
            source.mkdir()
            source.joinpath(".env").write_text("placeholder")
            result = ToolExecutor(tmp).copy_file("source", "copy")
            self.assertFalse(result.success)
            self.assertIn("protected", result.error.lower())
            self.assertFalse(Path(tmp, "copy").exists())

    @unittest.skipIf(not hasattr(__import__('os'), 'symlink'), 'symlinks unavailable')
    def test_symlink_to_protected_file_is_blocked(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, '.env').write_text('SECRET=value')
            try:
                os.symlink(Path(tmp, '.env'), Path(tmp, 'innocent.txt'))
            except OSError as exc:
                self.skipTest(f"symlinks are unavailable on this host: {exc}")
            result = ToolExecutor(tmp).read_file('innocent.txt')
            self.assertFalse(result.success)
            self.assertIn('protected', result.error.lower())

    @unittest.skipIf(not hasattr(__import__('os'), 'symlink'), 'symlinks unavailable')
    def test_command_script_symlink_escape_is_blocked(self):
        import os
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            Path(outside, 'evil.py').write_text('print(1)')
            try:
                os.symlink(outside, Path(root, 'escape'), target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symlinks are unavailable on this host: {exc}")
            result = ToolExecutor(root).run_command(['python', 'escape/evil.py'])
            self.assertFalse(result.success)
            self.assertIn('escapes workspace', result.error.lower())

    @unittest.skipIf(not hasattr(__import__('os'), 'symlink'), 'symlinks unavailable')
    def test_recursive_copy_rejects_nested_symlink_escape(self):
        import os
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            source = Path(root, "source")
            source.mkdir()
            Path(outside, "secret.txt").write_text("not actually secret")
            try:
                os.symlink(outside, source / "nested", target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symlinks are unavailable on this host: {exc}")
            result = ToolExecutor(root).copy_file("source", "copy")
            self.assertFalse(result.success)
            self.assertIn("escapes workspace", result.error.lower())
            self.assertFalse(Path(root, "copy").exists())
