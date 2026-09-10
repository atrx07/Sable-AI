import tempfile
import unittest
from pathlib import Path

from sable.context import ContextEngine, RepositoryContext
from sable.tools import ToolResult
from sable.verification import (
    AffectedTestSelector,
    CheckCategory,
    CheckStatus,
    FailureClassification,
    FailureClassifier,
    VerificationCheck,
    VerificationDiscovery,
    VerificationPlanner,
    VerificationScope,
)
from sable.verifier import Verifier


def write(root, relative, content=""):
    path = Path(root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def check(category=CheckCategory.UNIT_TEST, name="test"):
    return VerificationCheck.create(name, category, ["python", "--version"])


class AffectedSelectionTests(unittest.TestCase):
    def test_context_relationship_reverse_import_and_changed_test_are_selected(self):
        context = RepositoryContext(
            "/repo", "/repo",
            files=["pkg/core.py", "pkg/service.py", "tests/test_core.py", "tests/test_service.py", "tests/test_other.py"],
            importers={"pkg/core.py": ["pkg/service.py", "tests/test_core.py"]},
            test_relationships={"pkg/service.py": ["tests/test_service.py"]},
        )
        selected = AffectedTestSelector(".").select(("pkg/core.py", "tests/test_core.py"), context)
        self.assertEqual(selected.targets, ("tests/test_core.py", "tests/test_service.py"))
        self.assertNotIn("tests/test_other.py", selected.targets)
        self.assertTrue(any("changed test file" in reason for reason in selected.reasons))

    def test_direct_filename_match_and_selection_budget(self):
        context = RepositoryContext(
            "/repo", "/repo",
            files=["src/auth.py", "tests/test_auth.py", "tests/test_other.py"],
        )
        selected = AffectedTestSelector(".", max_targets=1).select(("src/auth.py",), context)
        self.assertEqual(selected.targets, ("tests/test_auth.py",))
        self.assertEqual(selected.candidates_avoided, 1)

    def test_high_fanout_module_expands_to_second_level_tests(self):
        context = RepositoryContext(
            "/repo", "/repo",
            files=["core.py", "a.py", "b.py", "feature.py", "tests/test_feature.py"],
            importers={"core.py": ["a.py", "b.py"], "a.py": ["feature.py"]},
            test_relationships={"feature.py": ["tests/test_feature.py"]},
        )
        ordinary = AffectedTestSelector(".", central_fanout=3).select(("core.py",), context)
        central = AffectedTestSelector(".", central_fanout=2).select(("core.py",), context)
        self.assertEqual(ordinary.targets, ())
        self.assertEqual(central.targets, ("tests/test_feature.py",))
        self.assertEqual(central.central_files, ("core.py",))

    def test_real_context_engine_targets_python_tests_in_non_git_workspace(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "pkg/__init__.py")
            write(root, "pkg/auth.py", "def login(): return True\n")
            write(root, "tests/test_auth.py", "from pkg.auth import login\n")
            write(root, "tests/test_other.py", "def test_other(): pass\n")
            context = ContextEngine(root).build()
            selected = AffectedTestSelector(root).select(("pkg/auth.py",), context)
            self.assertIn("tests/test_auth.py", selected.targets)
            self.assertNotIn("tests/test_other.py", selected.targets)

    def test_python_checks_are_refined_to_changed_source_and_affected_test(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "app.py", "def value(): return 1\n")
            write(root, "tests/test_app.py", "from app import value\n")
            planner = VerificationPlanner(root)
            planner.discovery = VerificationDiscovery(root, executable_finder=lambda name: f"/bin/{name}")
            plan = planner.plan(["app.py"], scope="affected")
            checks = {item.name: item for item in plan.checks}
            self.assertEqual(checks["Python syntax"].argv[:3], ("python", "-m", "py_compile"))
            self.assertIn("tests/test_app.py", checks["Python unit tests"].argv)
            self.assertTrue(checks["Python unit tests"].target_reasons)
            self.assertEqual(plan.affected_test_count, 1)

    def test_node_direct_runner_uses_proximate_test_but_script_falls_back_safely(self):
        context = RepositoryContext(
            "/repo", "/repo",
            files=["src/foo.ts", "src/foo.test.ts", "src/other.test.ts"],
        )
        selector = AffectedTestSelector(".")
        direct = VerificationCheck.create(
            "Vitest", CheckCategory.UNIT_TEST, ["vitest", "run"],
            language="JavaScript/TypeScript", scope=VerificationScope.AFFECTED,
        )
        script = direct.with_updates(name="npm test", argv=("npm", "run", "test"))
        refined, selected = selector.refine_checks([direct, script], ("src/foo.ts",), context, VerificationScope.AFFECTED)
        self.assertIn("src/foo.test.ts", refined[0].argv)
        self.assertEqual(refined[1].argv, ("npm", "run", "test"))
        self.assertNotIn("src/other.test.ts", selected.targets)


class ScopeEscalationTests(unittest.TestCase):
    def test_config_change_escalates_affected_to_full(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "package.json", '{"scripts":{"test":"ok","build":"ok"}}')
            planner = VerificationPlanner(root)
            planner.discovery = VerificationDiscovery(root, executable_finder=lambda name: f"/bin/{name}")
            plan = planner.plan(["package.json"], scope="affected")
            self.assertEqual(plan.requested_scope, VerificationScope.AFFECTED)
            self.assertEqual(plan.scope, VerificationScope.FULL)
            self.assertTrue(plan.scope_escalated)
            self.assertIn("npm build", {item.name for item in plan.checks})

    def test_ci_workflow_change_escalates_scope(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "app.py")
            write(root, ".github/workflows/ci.yml", "name: CI\n")
            planner = VerificationPlanner(root)
            planner.discovery = VerificationDiscovery(root, executable_finder=lambda name: f"/bin/{name}")
            self.assertEqual(planner.plan([".github/workflows/ci.yml"]).scope, VerificationScope.FULL)

    def test_custom_run_override_is_not_scope_escalated(self):
        with tempfile.TemporaryDirectory() as root:
            planner = VerificationPlanner(root)
            plan = planner.plan(["pyproject.toml"], scope="quick", custom_command="python --version")
            self.assertEqual(plan.scope, VerificationScope.QUICK)
            self.assertFalse(plan.scope_escalated)

    def test_verifier_considers_manifest_changes_verifiable(self):
        self.assertTrue(Verifier.needs_verification(["pyproject.toml"]))
        self.assertTrue(Verifier.needs_verification(["frontend/package.json"]))


class FailureClassifierTests(unittest.TestCase):
    def setUp(self):
        self.classifier = FailureClassifier()

    def classify(self, text, *, category=CheckCategory.UNIT_TEST, status=CheckStatus.FAIL):
        item = check(category)
        result = ToolResult("run_command", False, error=text)
        return self.classifier.analyze(item, status, result)

    def test_syntax_import_assertion_collection_and_unknown(self):
        cases = (
            ("SyntaxError: invalid syntax", FailureClassification.SYNTAX_ERROR),
            ("ImportError: cannot import name thing", FailureClassification.IMPORT_ERROR),
            ("AssertionError: expected 1 but got 2", FailureClassification.ASSERTION_FAILURE),
            ("ERROR collecting tests/test_app.py", FailureClassification.TEST_COLLECTION_FAILURE),
            ("process exited strangely", FailureClassification.UNKNOWN_FAILURE),
        )
        for message, expected in cases:
            with self.subTest(message=message):
                self.assertEqual(self.classify(message)[0], expected)

    def test_category_classification_for_lint_type_and_build(self):
        cases = (
            (CheckCategory.LINT, FailureClassification.LINT_ERROR),
            (CheckCategory.TYPECHECK, FailureClassification.TYPE_ERROR),
            (CheckCategory.BUILD, FailureClassification.BUILD_ERROR),
        )
        for category, expected in cases:
            with self.subTest(category=category):
                self.assertEqual(self.classify("check failed", category=category)[0], expected)

    def test_dependency_tool_timeout_policy_and_resource_classification(self):
        self.assertEqual(self.classify("ModuleNotFoundError: No module named 'demo'")[0], FailureClassification.DEPENDENCY_MISSING)
        self.assertEqual(self.classify("missing", status=CheckStatus.SKIPPED_UNAVAILABLE)[0], FailureClassification.TOOL_MISSING)
        self.assertEqual(self.classify("timed out", status=CheckStatus.TIMEOUT)[0], FailureClassification.TIMEOUT)
        self.assertEqual(self.classify("denied", status=CheckStatus.BLOCKED)[0], FailureClassification.POLICY_BLOCKED)
        self.assertEqual(self.classify("MemoryError: out of memory")[0], FailureClassification.RESOURCE_LIMIT)

    def test_diagnostic_is_bounded_and_secret_redacted(self):
        secret = "GROQ_API_KEY=gsk_abcdefghijklmnopqrstuvwxyz123456"
        classification, diagnostic, signature = self.classify(secret + "\n" + ("failure line\n" * 2000))
        self.assertEqual(classification, FailureClassification.UNKNOWN_FAILURE)
        self.assertLessEqual(len(diagnostic), 4000)
        self.assertNotIn("gsk_", diagnostic)
        self.assertTrue(signature.startswith("failure-"))

    def test_failure_signature_normalizes_line_numbers_and_durations(self):
        first = self.classify("AssertionError at file.py:12 line 12 after 1.2s")[2]
        second = self.classify("AssertionError at file.py:99 line 99 after 9.9s")[2]
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
