import tempfile
import unittest
from pathlib import Path

from scripts.release import (
    checksum_text,
    publication_requested,
    release_notes,
    require_successful_run,
    validate_tag,
    verify_checksums,
)


class ReleaseGateTests(unittest.TestCase):
    def test_exact_tag_version_match(self):
        validate_tag("v2.0.0", "2.0.0")
        for tag in ("v2.0.1", "2.0.0", "v2.0.0;echo unsafe", ""):
            with self.assertRaises(ValueError):
                validate_tag(tag, "2.0.0")

    def test_checksum_order_is_deterministic_and_tampering_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pkg.whl").write_bytes(b"wheel")
            (root / "pkg.tar.gz").write_bytes(b"source")
            text = checksum_text(root)
            self.assertEqual(text, checksum_text(root))
            self.assertEqual(
                [line.split("  ")[1] for line in text.splitlines()], ["pkg.tar.gz", "pkg.whl"]
            )
            (root / "SHA256SUMS.txt").write_text(text, encoding="utf-8")
            verify_checksums(root)
            (root / "pkg.whl").write_bytes(b"changed")
            with self.assertRaises(ValueError):
                verify_checksums(root)

    def test_dry_run_is_default_even_with_unrelated_credentials(self):
        self.assertFalse(publication_requested({"GITHUB_TOKEN": "synthetic-not-for-metadata"}))
        notes = release_notes("## [Unreleased]\nCurated change.\n", "2.0.0", publishing=False)
        self.assertNotIn("synthetic-not-for-metadata", notes)

    def test_publication_requires_manual_tag_and_owner_enablement(self):
        env = {"PUBLISH_GITHUB": "true"}
        with self.assertRaises(ValueError):
            publication_requested(env)
        env.update(GITHUB_EVENT_NAME="workflow_dispatch", GITHUB_REF="refs/tags/v2.0.0")
        with self.assertRaises(ValueError):
            publication_requested(env)
        env["SABLE_RELEASE_ENABLED"] = "true"
        self.assertTrue(publication_requested(env))
        env["PUBLISH_PYPI"] = "true"
        with self.assertRaises(ValueError):
            publication_requested(env)
        env["SABLE_PYPI_ENABLED"] = "true"
        self.assertTrue(publication_requested(env))

    def test_release_requires_curated_version_section(self):
        text = "## [Unreleased]\nFuture work.\n## [2.0.0] - owner date\nApproved fixes.\n## [1.0.0]\nOld work.\n"
        notes = release_notes(text, "2.0.0", publishing=True)
        self.assertIn("Approved fixes", notes)
        self.assertNotIn("Future work", notes)
        self.assertNotIn("Old work", notes)
        self.assertIn(
            "Approved fixes",
            release_notes(
                "## [Unreleased]\n\n## [2.0.0]\nApproved fixes.", "2.0.0", publishing=False
            ),
        )
        with self.assertRaises(ValueError):
            release_notes(text, "2.1.0", publishing=True)

    def test_required_ci_must_match_exact_sha_and_succeed(self):
        require_successful_run([{"headSha": "wanted", "conclusion": "success"}], "wanted")
        for runs in (
            [],
            [{"headSha": "other", "conclusion": "success"}],
            [{"headSha": "wanted", "conclusion": "failure"}],
        ):
            with self.assertRaises(ValueError):
                require_successful_run(runs, "wanted")
