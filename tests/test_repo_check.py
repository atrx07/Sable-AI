import tempfile
import unittest
from pathlib import Path

from scripts.repo_check import broken_links, workflow_errors


class DocumentationLinkTests(unittest.TestCase):
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
