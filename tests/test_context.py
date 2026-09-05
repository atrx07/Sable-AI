import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from sable.context import ContextEngine
from sable.main_agent import MainAgent
from sable.providers import ModelRouter
from sable.security import READ_ONLY_TOOLS
from sable.tools import ToolExecutor


class ContextEngineTests(unittest.TestCase):
    def make_repo(self, root: Path) -> None:
        (root / "pkg").mkdir()
        (root / "tests").mkdir()
        (root / "pyproject.toml").write_text("[project]\nname='demo'\n")
        (root / "pkg" / "utils.py").write_text("def helper(value):\n    return value\n")
        (root / "pkg" / "auth.py").write_text(
            '"""Authentication helpers."""\n'
            "from .utils import helper\n\n"
            "class AuthService:\n"
            "    async def login(self, user):\n"
            "        return helper(user)\n\n"
            "def authenticate(user):\n"
            "    return AuthService()\n"
        )
        (root / "tests" / "test_auth.py").write_text(
            "from pkg.auth import AuthService, authenticate\n\n"
            "def test_authenticate():\n"
            "    assert authenticate('u')\n"
        )
        (root / "broken.py").write_text("def broken(:\n")
        (root / ".env").write_text("API_KEY=should-never-appear\n")

    def test_python_symbols_imports_tests_and_invalid_syntax(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_repo(root)
            context = ContextEngine(root).build()
            names = {symbol.qualified_name: symbol.kind for symbol in context.symbols}
            self.assertIn("Python", context.languages)
            self.assertEqual(names["AuthService"], "class")
            self.assertEqual(names["AuthService.login"], "async_method")
            self.assertEqual(names["authenticate"], "function")
            self.assertIn(".utils", context.imports["pkg/auth.py"])
            self.assertIn("pkg/auth.py", context.importers["pkg/utils.py"])
            self.assertIn("tests/test_auth.py", context.test_relationships["pkg/auth.py"])
            self.assertTrue(any(item.startswith("broken.py:") for item in context.syntax_errors))
            self.assertNotIn(".env", context.files)
            self.assertNotIn("should-never-appear", str(context.to_dict()))
            selection = ContextEngine(root).select("change AuthService authentication")
            selected = {item.path for item in selection.items}
            self.assertIn("pkg/auth.py", selected)
            self.assertIn("tests/test_auth.py", selected)

    def test_git_dirty_and_recent_context_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_repo(root)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True)
            (root / "pkg" / "auth.py").write_text("def changed():\n    return True\n")
            (root / "new.py").write_text("NEW = True\n")
            context = ContextEngine(root).build()
            self.assertTrue(context.git["present"])
            self.assertIn("pkg/auth.py", context.git["unstaged"])
            self.assertIn("new.py", context.git["untracked"])
            self.assertLessEqual(len(context.recent_commits), 5)
            self.assertTrue(context.git["head"])

    def test_repository_map_and_selection_budget_are_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            for index in range(100):
                (root / "src" / f"feature_{index}.py").write_text(f"def feature_{index}():\n    return {index}\n")
            engine = ContextEngine(root, map_character_budget=500, selection_character_budget=600, max_selected_files=3)
            context = engine.build()
            selection = engine.select("fix feature_42")
            self.assertLessEqual(len(context.repository_map), 520)
            self.assertLessEqual(selection.characters_used, 600)
            self.assertLessEqual(len(selection.items), 3)
            self.assertTrue(selection.truncated)
            self.assertTrue(any(item.path.endswith("feature_42.py") for item in selection.items))
            self.assertTrue(any(item.reasons for item in selection.items))

    def test_cache_invalidates_when_file_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "module.py"
            target.write_text("def first():\n    pass\n")
            engine = ContextEngine(root)
            first = engine.build()
            target.write_text("def second_name():\n    return 2\n")
            os.utime(target, None)
            second = engine.build()
            self.assertIsNot(first, second)
            self.assertIn("second_name", {item.qualified_name for item in second.symbols})

    def test_escaping_directory_symlink_is_excluded(self):
        with tempfile.TemporaryDirectory() as root_tmp, tempfile.TemporaryDirectory() as outside_tmp:
            root = Path(root_tmp)
            outside = Path(outside_tmp)
            (outside / "secret.py").write_text("SECRET = True\n")
            try:
                (root / "escape").symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            context = ContextEngine(root).build()
            self.assertFalse(any(path.startswith("escape/") for path in context.files))


class ContextToolTests(unittest.TestCase):
    def test_context_tools_are_read_only_and_plan_compatible(self):
        expected = {"repo_map", "list_symbols", "find_symbol", "find_references", "read_symbol", "find_tests_for_file", "recent_changes"}
        self.assertTrue(expected.issubset(READ_ONLY_TOOLS))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "module.py").write_text(
                "HEADER = 1\n\n"
                "def target(value):\n"
                "    return value + 1\n\n"
                + "\n".join(f"TRAIL_{index} = {index}" for index in range(100))
            )
            executor = ToolExecutor(tmp)
            for tool, args in (
                ("repo_map", {}),
                ("list_symbols", {"path": "module.py"}),
                ("find_symbol", {"name": "target"}),
                ("read_symbol", {"name": "target", "path": "module.py"}),
                ("find_references", {"name": "target"}),
                ("find_tests_for_file", {"path": "module.py"}),
                ("recent_changes", {}),
            ):
                result = executor.dispatch(tool, args, mode="plan")
                self.assertTrue(result.success, (tool, result.error))
                self.assertFalse(result.changed_files)
                self.assertLessEqual(len(result.output), 12000)
            symbol = executor.read_symbol("target", "module.py")
            self.assertIn("def target", symbol.output)
            self.assertNotIn("TRAIL_99", symbol.output)

    def test_context_tools_reject_protected_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("TOKEN=hidden")
            executor = ToolExecutor(tmp)
            self.assertFalse(executor.list_symbols(".env").success)
            self.assertFalse(executor.read_symbol("TOKEN", ".env").success)


class CapturingProvider:
    name = "fake"

    def __init__(self, model, content):
        self.model = model
        self.content = content
        self.messages = []

    def complete(self, messages, **kwargs):
        self.messages.append(messages)
        return {"content": self.content, "tool_calls": [], "usage": {"total_tokens": 2}}


class ContextRoutingTests(unittest.TestCase):
    def test_large_context_uses_tool_free_fast_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            for index in range(100):
                (root / "src" / f"module_{index}.py").write_text(f"def item_{index}():\n    return {index}\n")
            executor = ToolExecutor(tmp)
            executor.context_engine = ContextEngine(root, map_character_budget=500, selection_character_budget=600)
            main = CapturingProvider("main-model", "done")
            fast = CapturingProvider("fast-model", "compressed repository context")
            agent = MainAgent(main, executor, router=ModelRouter(main, fast))

            result = agent.run("change item_42")

            self.assertEqual(len(fast.messages), 1)
            self.assertNotIn("tools", fast.messages[0][0])
            self.assertIn("compressed repository context", main.messages[0][0]["content"])
            self.assertEqual(result["routing_purposes"], ["FAST_CONTEXT_SUMMARY", "MAIN_REASONING"])
            self.assertEqual(result["model_calls"], 2)
