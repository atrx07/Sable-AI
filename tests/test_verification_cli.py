import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from sable.cli_settings import SettingsCommandsMixin
from sable.config import DEFAULTS


class DummySettings(SettingsCommandsMixin):
    def __init__(self):
        self.cfg = dict(DEFAULTS)
        self.verify_enabled = True
        self.verification_scope = "affected"


class VerificationCliTests(unittest.TestCase):
    def invoke(self, settings, value):
        with patch("sable.cli_settings.save_config") as save, redirect_stdout(io.StringIO()) as output:
            settings._cmd_verify(value)
        return save, output.getvalue()

    def test_default_scope_is_affected(self):
        self.assertEqual(DEFAULTS["verification_scope"], "affected")

    def test_legacy_toggle_remains_supported(self):
        settings = DummySettings()
        save, output = self.invoke(settings, "off")
        self.assertFalse(settings.verify_enabled)
        self.assertEqual(settings.verification_scope, "affected")
        self.assertIn("OFF (affected)", output)
        save.assert_called_once()

    def test_scope_short_and_explicit_forms_are_persisted(self):
        for command, expected in (("quick", "quick"), ("scope full", "full")):
            with self.subTest(command=command):
                settings = DummySettings()
                save, output = self.invoke(settings, command)
                self.assertEqual(settings.verification_scope, expected)
                self.assertEqual(settings.cfg["verification_scope"], expected)
                self.assertIn(f"ON ({expected})", output)
                save.assert_called_once()

    def test_invalid_scope_does_not_mutate_config(self):
        settings = DummySettings()
        save, output = self.invoke(settings, "scope enormous")
        self.assertEqual(settings.verification_scope, "affected")
        self.assertIn("Usage:", output)
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
