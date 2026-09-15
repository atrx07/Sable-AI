import tempfile
import unittest
from pathlib import Path

from scripts.repo_check import broken_links


class DocumentationLinkTests(unittest.TestCase):
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
