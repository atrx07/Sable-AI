import unittest
from unittest.mock import patch

from sable.cli_settings import SettingsCommandsMixin


class _FakeRenderer:
    def __init__(self):
        self.messages = []

    def message(self, text, error=False):
        self.messages.append(str(text))


class _KeysHarness(SettingsCommandsMixin):
    def __init__(self, cfg):
        self.cfg = cfg
        self.renderer = _FakeRenderer()

    def _rebuild_agents(self):
        pass


class KeysDisclaimerTests(unittest.TestCase):
    @patch("sable.cli_settings.save_config", lambda cfg: None)
    @patch("sable.cli_settings.getpass", return_value="")
    def test_groq_keys_shows_free_tier_disclaimer(self, _getpass):
        cfg = {
            "provider": "groq",
            "groq_key_1": "",
            "groq_key_2": "",
            "groq_key_3": "",
            "active_key_index": 1,
        }
        harness = _KeysHarness(cfg)
        harness._cmd_keys("")
        joined = "\n".join(harness.renderer.messages)
        self.assertIn("8,000 tokens/minute", joined)
        self.assertIn("vary by model and organization", joined)
        self.assertIn("take longer", joined)
