import tempfile
import unittest
from pathlib import Path

from scripts.release_check import SMOKE_ENV_KEYS, artifacts, validate_paths


class ArtifactPolicyTests(unittest.TestCase):
    def test_artifacts_require_exact_pair_in_sorted_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(ValueError):
                artifacts(root)
            (root / "sable.whl").touch()
            (root / "sable.tar.gz").touch()
            self.assertEqual([p.name for p in artifacts(root)], ["sable.tar.gz", "sable.whl"])
            (root / "old.whl").touch()
            with self.assertRaises(ValueError):
                artifacts(root)

    def test_rejects_private_generated_and_traversal_paths(self):
        for name in [
            "../secret",
            "/absolute",
            "sable/.env",
            "sable/__pycache__/a.pyc",
            "sable/traces/x.json",
            "tests/x.py",
        ]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_paths([name], wheel=True)
        for name in [
            "pkg/.git/config",
            "pkg/evals/reports/generated/report.json",
            "pkg/.sable/config.json",
        ]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_paths([name], wheel=False)

    def test_smoke_env_keeps_proxy_variables_for_proxied_hosts(self):
        # The clean-install smoke runs pip in a sanitized environment; without
        # the proxy variables it cannot reach PyPI from a proxied host.
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY"):
            with self.subTest(key=key):
                self.assertIn(key, SMOKE_ENV_KEYS)
        # Provider credentials must never be in the smoke environment.
        for key in ("GROQ_API_KEY", "OPENAI_API_KEY", "SABLE_API_KEY"):
            with self.subTest(key=key):
                self.assertNotIn(key, SMOKE_ENV_KEYS)

    def test_allows_runtime_metadata_and_canonical_source_assets(self):
        validate_paths(
            ["sable/cli_app.py", "sable_ai_agent-2.0.0.dist-info/licenses/LICENSE"], wheel=True
        )
        validate_paths(
            ["pkg/evals/baselines/m7-deterministic.json", "pkg/tests/test_cli.py"], wheel=False
        )
