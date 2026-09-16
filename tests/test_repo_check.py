import tempfile
import unittest
from pathlib import Path

from scripts.repo_check import broken_links, readme_command_errors, showcase_errors, workflow_errors


class DocumentationLinkTests(unittest.TestCase):
    def test_readme_commands_use_the_real_parser_contract(self):
        self.assertEqual(
            readme_command_errors(
                '```bash\nsable .\nsable run "Fix tests" . --json\nsable doctor .\n```'
            ),
            [],
        )
        errors = readme_command_errors("```bash\nsable unknown-command --json\n```")
        self.assertTrue(errors)

    def test_showcase_facts_follow_version_baseline_and_diagrams(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "evals/baselines").mkdir(parents=True)
            (root / "sable").mkdir()
            (root / "docs").mkdir()
            (root / "evals/baselines/m7-deterministic.json").write_text(
                '{"scenario_ids":["one","two"]}', encoding="utf-8"
            )
            (root / "sable/_version.py").write_text('__version__ = "9.1.0"\n', encoding="utf-8")
            (root / "README.md").write_text("Sable 9.1.0 has 2 scenarios", encoding="utf-8")
            (root / "docs/release-notes-draft.md").write_text(
                "9.1.0: 2 synthetic scenarios", encoding="utf-8"
            )
            (root / "docs/launch-kit.md").write_text(
                "version: 9.1.0 and 2 scenarios", encoding="utf-8"
            )
            (root / "docs/demo.md").write_text("complete 2-scenario baseline", encoding="utf-8")
            (root / "docs/architecture.md").write_text(
                "\n".join(["```mermaid\nflowchart LR\n```"] * 6), encoding="utf-8"
            )
            self.assertEqual(showcase_errors(root), [])
            (root / "README.md").write_text("stale claims", encoding="utf-8")
            self.assertTrue(showcase_errors(root))

    def test_workflow_gate_rejects_floating_actions_and_excessive_permissions(self):
        value = {
            "permissions": {"contents": "read"},
            "jobs": {
                "test": {
                    "permissions": {"contents": "write"},
                    "steps": [{"uses": "actions/checkout@v7"}],
                }
            },
        }
        errors = workflow_errors(value, "ci.yml")
        self.assertEqual(len(errors), 2)

    def test_release_gate_rejects_automatic_triggers_and_missing_publication_guards(self):
        errors = workflow_errors(
            {"permissions": {"contents": "read"}, "on": {"push": {}}}, "release.yml"
        )
        self.assertIn("Release workflow must remain manual-dispatch only", errors)
        self.assertTrue(any("guards are missing" in error for error in errors))

    def test_local_target_external_url_and_anchor(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "guide.md").touch()
            doc = root / "README.md"
            doc.write_text(
                "[ok](guide.md#anchor) [web](https://example.invalid/x) [self](#x)",
                encoding="utf-8",
            )
            self.assertEqual(broken_links(doc, root), [])

    def test_missing_and_escaping_targets_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            doc = root / "README.md"
            doc.write_text("[missing](missing.md) [escape](../outside.md)", encoding="utf-8")
            self.assertEqual(len(broken_links(doc, root)), 2)

    def test_code_examples_are_not_treated_as_real_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            doc = root / "README.md"
            doc.write_text("```markdown\n[example](missing.md)\n```", encoding="utf-8")
            self.assertEqual(broken_links(doc, root), [])
