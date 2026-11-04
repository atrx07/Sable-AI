import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sable.evals import cli


class EvalCliWheelInstallTests(unittest.TestCase):
    def test_missing_scenario_data_fails_with_plain_language_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for argv in (["--scenario", "coding.simple_bug"], ["--live"]):
                with (
                    self.subTest(argv=argv),
                    mock.patch.object(cli, "_root", return_value=root),
                    self.assertRaises(SystemExit) as caught,
                ):
                    cli.main(argv)
                message = str(caught.exception.code)
                self.assertIn("not shipped in the installed package", message)
                self.assertIn("source checkout", message)
                self.assertIn("extracted sdist", message)
