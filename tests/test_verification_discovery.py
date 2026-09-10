import json
import os
import tempfile
import unittest
from pathlib import Path

from sable.verification import (
    CheckAvailability,
    CheckCategory,
    VerificationDiscovery,
    VerificationPlanner,
    VerificationCheck,
)
from sable.security import PermissionPolicy


def write(root, relative, content=""):
    path = Path(root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def finder(*available):
    names = set(available)
    return lambda executable: f"/tools/{executable}" if executable in names else None


def by_name(result):
    return {check.name: check for check in result.checks}


class PythonDiscoveryTests(unittest.TestCase):
    def test_unittest_project_gets_syntax_and_test_checks(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "app.py", "value = 1\n")
            write(root, "tests/test_app.py", "import unittest\n")
            result = VerificationDiscovery(root, executable_finder=finder("python")).discover(["app.py"])
            checks = by_name(result)
            self.assertEqual(set(checks), {"Python syntax", "Python unit tests"})
            self.assertEqual(checks["Python syntax"].scope.value, "QUICK")

    def test_pyproject_pytest_and_optional_tools_are_manifest_driven(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "pyproject.toml", """
[tool.pytest.ini_options]
[tool.ruff]
[tool.black]
[tool.mypy]
[tool.pyright]
""")
            result = VerificationDiscovery(
                root, executable_finder=finder("python", "pytest", "ruff", "black", "mypy", "pyright")
            ).discover(["src/app.py"])
            checks = by_name(result)
            self.assertTrue({"pytest", "Ruff", "Black check", "MyPy", "Pyright"}.issubset(checks))
            self.assertTrue(checks["pytest"].required)
            self.assertFalse(checks["Ruff"].required)

    def test_configured_missing_python_tool_is_explicitly_unavailable(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "ruff.toml", "line-length = 100\n")
            write(root, "app.py")
            result = VerificationDiscovery(root, executable_finder=finder("python")).discover(["app.py"])
            self.assertEqual(by_name(result)["Ruff"].availability, CheckAvailability.UNAVAILABLE)

    def test_setup_cfg_and_standalone_configs_are_recognized(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "setup.cfg", "[tool:pytest]\n[flake8]\n[mypy]\n")
            write(root, "app.py")
            result = VerificationDiscovery(root, executable_finder=finder()).discover(["app.py"])
            self.assertTrue({"pytest", "Flake8", "MyPy"}.issubset(by_name(result)))

    def test_malformed_pyproject_warns_but_does_not_abort(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "pyproject.toml", "[tool.ruff\nline-length = 100\n")
            result = VerificationDiscovery(root, executable_finder=finder("python")).discover(["app.py"])
            self.assertIn("Python syntax", by_name(result))
            self.assertTrue(any("Malformed TOML" in warning for warning in result.warnings))


class NodeDiscoveryTests(unittest.TestCase):
    def test_known_scripts_are_selected_and_unsafe_scripts_ignored(self):
        with tempfile.TemporaryDirectory() as root:
            package = {"scripts": {
                "test": "vitest run", "lint": "eslint .", "typecheck": "tsc --noEmit",
                "build": "vite build", "deploy": "ship", "release": "publish",
            }}
            write(root, "package.json", json.dumps(package))
            result = VerificationDiscovery(root, executable_finder=finder("npm")).discover(["src/app.ts"])
            names = set(by_name(result))
            self.assertEqual(names, {"npm lint", "npm typecheck", "npm test", "npm build"})
            self.assertNotIn("deploy", " ".join(names).lower())

    def test_package_manager_lock_preference(self):
        for lockfile, manager in (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("package-lock.json", "npm")):
            with self.subTest(lockfile=lockfile), tempfile.TemporaryDirectory() as root:
                write(root, "package.json", json.dumps({"scripts": {"test": "runner"}}))
                write(root, lockfile)
                result = VerificationDiscovery(root, executable_finder=finder(manager)).discover(["index.js"])
                self.assertEqual(result.checks[0].argv[:3], (manager, "run", "test"))

    def test_missing_node_modules_never_triggers_install(self):
        with tempfile.TemporaryDirectory() as root:
            package = {"scripts": {"test": "vitest run"}, "devDependencies": {"vitest": "1.0.0"}}
            write(root, "package.json", json.dumps(package))
            result = VerificationDiscovery(root, executable_finder=finder("npm", "vitest")).discover(["index.ts"])
            check = by_name(result)["npm test"]
            self.assertEqual(check.availability, CheckAvailability.UNAVAILABLE)
            self.assertNotIn("install", check.argv)
            self.assertIn("not installed", check.availability_reason)

    def test_tsconfig_jest_vitest_and_eslint_configs_create_direct_checks(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "tsconfig.json", "{}")
            write(root, "eslint.config.js", "export default []")
            write(root, "vitest.config.ts", "export default {}")
            write(root, "jest.config.js", "module.exports = {}")
            result = VerificationDiscovery(
                root, executable_finder=finder("tsc", "eslint", "vitest", "jest")
            ).discover(["src/app.ts"])
            self.assertTrue({"TypeScript", "ESLint", "Vitest", "Jest"}.issubset(by_name(result)))

    def test_malformed_package_json_is_bounded_to_a_warning(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "package.json", "{not-json")
            result = VerificationDiscovery(root, executable_finder=finder("npm")).discover(["index.js"])
            self.assertEqual(result.checks, ())
            self.assertTrue(any("Malformed package.json" in warning for warning in result.warnings))


class CompiledLanguageDiscoveryTests(unittest.TestCase):
    def test_cargo_workspace_checks_are_offline_and_availability_is_honest(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "Cargo.toml", '[workspace]\nmembers = ["core"]\n')
            result = VerificationDiscovery(root, executable_finder=finder()).discover(["core/src/lib.rs"])
            checks = by_name(result)
            self.assertTrue({"cargo check", "cargo test", "cargo fmt", "cargo clippy"}.issubset(checks))
            self.assertIn("--offline", checks["cargo check"].argv)
            self.assertEqual(checks["cargo test"].availability, CheckAvailability.UNAVAILABLE)

    def test_go_module_discovers_test_and_vet_without_mutating_modules(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "go.mod", "module example.com/demo\n")
            result = VerificationDiscovery(root, executable_finder=finder("go")).discover(["main.go"])
            checks = by_name(result)
            self.assertEqual(set(checks), {"go test", "go vet"})
            self.assertIn("-mod=readonly", checks["go test"].argv)

    def test_maven_wrapper_is_preferred_and_system_tool_can_be_unavailable(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "pom.xml", "<project/>")
            wrapper_name = "mvnw.cmd" if os.name == "nt" else "mvnw"
            write(root, wrapper_name)
            wrapped = VerificationDiscovery(root, executable_finder=finder()).discover(["src/Main.java"])
            self.assertIn("mvnw", by_name(wrapped)["Maven test"].argv[0])
            self.assertEqual(by_name(wrapped)["Maven test"].availability, CheckAvailability.AVAILABLE)
        with tempfile.TemporaryDirectory() as root:
            write(root, "pom.xml", "<project/>")
            system = VerificationDiscovery(root, executable_finder=finder()).discover(["src/Main.java"])
            self.assertEqual(by_name(system)["Maven test"].availability, CheckAvailability.UNAVAILABLE)

    def test_gradle_wrapper_is_preferred(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "build.gradle.kts", "plugins { java }")
            wrapper_name = "gradlew.bat" if os.name == "nt" else "gradlew"
            write(root, wrapper_name)
            result = VerificationDiscovery(root, executable_finder=finder()).discover(["src/Main.java"])
            checks = by_name(result)
            self.assertIn("gradlew", checks["Gradle test"].argv[0])
            self.assertIn("--offline", checks["Gradle check"].argv)


class ProjectRootDiscoveryTests(unittest.TestCase):
    def test_source_only_nested_python_layout_preserves_fallback_discovery(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "src/demo/app.py", "value = 1\n")
            result = VerificationDiscovery(root, executable_finder=finder("python")).discover(["src/demo/app.py"])
            self.assertIn("Python syntax", by_name(result))
            self.assertEqual(result.project_roots, (".",))

    def test_affected_scope_selects_nearest_nested_root(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "backend/pyproject.toml", "[project]\nname='backend'\n")
            write(root, "frontend/package.json", json.dumps({"scripts": {"test": "vitest run"}}))
            result = VerificationDiscovery(
                root, executable_finder=finder("python", "npm")
            ).discover(["frontend/src/app.ts"])
            self.assertEqual(result.project_roots, ("frontend",))
            self.assertEqual(result.adapters, ("NodeAdapter",))
            self.assertEqual(result.roots_avoided, 1)

    def test_full_scope_includes_all_bounded_project_roots(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "backend/pyproject.toml", "[project]\nname='backend'\n")
            write(root, "frontend/package.json", json.dumps({"scripts": {"test": "vitest run"}}))
            result = VerificationDiscovery(
                root, executable_finder=finder("python", "npm")
            ).discover(["frontend/src/app.ts"], scope="full")
            self.assertEqual(result.project_roots, ("backend", "frontend"))
            self.assertEqual(set(result.adapters), {"PythonAdapter", "NodeAdapter"})

    def test_planner_exposes_manifest_and_root_discovery(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "go.mod", "module demo\n")
            planner = VerificationPlanner(root)
            planner.discovery = VerificationDiscovery(root, executable_finder=finder("go"))
            plan = planner.plan(["main.go"])
            self.assertEqual(plan.discovered_manifests, ("go.mod",))
            self.assertEqual(plan.project_roots, (".",))
            self.assertIn("GoAdapter", plan.adapters)
            self.assertTrue(all(check.category in {CheckCategory.UNIT_TEST, CheckCategory.STATIC_ANALYSIS} for check in plan.checks))

    def test_availability_is_part_of_deterministic_check_identity(self):
        available = VerificationCheck.create(
            "lint", CheckCategory.LINT, ["ruff", "check", "."],
            availability=CheckAvailability.AVAILABLE,
        )
        unavailable = VerificationCheck.create(
            "lint", CheckCategory.LINT, ["ruff", "check", "."],
            availability=CheckAvailability.UNAVAILABLE,
        )
        self.assertNotEqual(available.check_id, unavailable.check_id)

    def test_new_verification_executables_remain_build_policy_checked(self):
        policy = PermissionPolicy("build")
        for executable in ("black", "flake8", "pyright", "tsc", "eslint", "vitest", "mvn", "gradle"):
            allowed, _ = policy.validate("run_command", {"argv": [executable, "--version"]})
            self.assertTrue(allowed, executable)
        allowed, _ = policy.validate("run_command", {"argv": ["curl", "https://example.com"]})
        self.assertFalse(allowed)

    def test_external_manifest_symlink_is_not_read_when_supported(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            external = write(outside, "package.json", json.dumps({"scripts": {"test": "secret"}}))
            link = Path(root, "package.json")
            try:
                link.symlink_to(external)
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            result = VerificationDiscovery(root, executable_finder=finder("npm")).discover(["index.js"])
            self.assertEqual(result.checks, ())


if __name__ == "__main__":
    unittest.main()
