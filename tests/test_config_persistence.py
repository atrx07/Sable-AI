import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sable import config
from sable.groq_client import GroqClient
from sable.ui import _mask


class ConfigPersistenceTests(unittest.TestCase):
    def test_key_display_is_independent_of_secret_content_and_length(self):
        for value in ("x", "short-key", "gsk_SYNTHETIC_ENVIRONMENT_ONLY_12345"):
            self.assertIn("(configured)", _mask(value))
            self.assertEqual(_mask(value), _mask("another-value"))
            self.assertNotIn(value, _mask(value))
        self.assertIn("(not set)", _mask(""))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "config.json"
        for patcher in [
            patch.object(config, "CONFIG_DIR", self.root),
            patch.object(config, "CONFIG_FILE", self.path),
            patch.dict(os.environ, {"GROQ_API_KEY": "gsk_SYNTHETIC_ENVIRONMENT_ONLY_12345"}),
        ]:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_fresh_model_defaults_and_offline_hints(self):
        cfg = config.load_config()
        self.assertEqual(cfg["main_model"], "openai/gpt-oss-120b")
        self.assertEqual(cfg["fast_model"], "openai/gpt-oss-20b")
        self.assertIn(cfg["main_model"], config.PRODUCTION_MODEL_HINTS)
        self.assertIn(cfg["fast_model"], config.PRODUCTION_MODEL_HINTS)
        self.assertNotIn("llama-3.1-8b-instant", config.PRODUCTION_MODEL_HINTS)
        self.assertNotIn("llama-3.3-70b-versatile", config.PRODUCTION_MODEL_HINTS)

    def test_explicit_fast_model_is_never_migrated(self):
        for model in ("some-explicit-model", "llama-3.1-8b-instant"):
            with self.subTest(model=model):
                self.path.write_text(json.dumps({"fast_model": model}), encoding="utf-8")
                self.assertEqual(config.load_config()["fast_model"], model)

    def test_missing_fast_model_inherits_current_default(self):
        self.path.write_text(json.dumps({"main_model": "saved-main"}), encoding="utf-8")
        cfg = config.load_config()
        self.assertEqual(cfg["main_model"], "saved-main")
        self.assertEqual(cfg["fast_model"], "openai/gpt-oss-20b")

    def test_environment_key_survives_use_but_never_persists(self):
        cfg = config.load_config()
        self.assertEqual(cfg["groq_key_1"], "")
        self.assertEqual(config.get_active_key(cfg), (os.environ["GROQ_API_KEY"], 1))
        self.assertEqual(config.configured_key_indices(cfg), [1])
        client = GroqClient(cfg, "unused-model")
        client._record_usage(1, {"usage": {"total_tokens": 7}})
        config.rotate_to_next_key(cfg)
        saved = self.path.read_text(encoding="utf-8")
        self.assertNotIn(os.environ["GROQ_API_KEY"], saved)
        self.assertEqual(json.loads(saved)["token_usage"]["1"], 7)

    def test_explicit_key_preserves_precedence_and_local_storage(self):
        cfg = config.load_config()
        cfg["groq_key_1"] = "explicit-synthetic-key"
        config.save_config(cfg)
        self.assertEqual(config.get_active_key(cfg), ("explicit-synthetic-key", 1))
        self.assertEqual(json.loads(self.path.read_text())["groq_key_1"], "explicit-synthetic-key")

    def test_failed_replace_preserves_previous_config_and_removes_temporary_file(self):
        config.save_config({"setting": "before"})
        with patch("sable.config.os.replace", side_effect=OSError("synthetic failure")):
            with self.assertRaises(OSError):
                config.save_config({"setting": "after"})
        self.assertEqual(json.loads(self.path.read_text()), {"setting": "before"})
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["config.json"])

    @unittest.skipIf(os.name == "nt", "POSIX file modes required")
    def test_saved_config_is_private(self):
        config.save_config({"groq_key_1": "synthetic"})
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
