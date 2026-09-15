import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GIT_BASH = Path("C:/Program Files/Git/bin/bash.exe")
BASH = str(GIT_BASH) if GIT_BASH.is_file() else shutil.which("bash")


class InstallerGuardTests(unittest.TestCase):
    @unittest.skipUnless(BASH, "Bash required")
    def test_non_termux_invocation_fails_before_installation(self):
        env = dict(os.environ, PREFIX="")
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                [BASH, (ROOT / "install.sh").as_posix()],
                cwd=temporary,
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("requires Termux", result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertEqual(list(Path(temporary).iterdir()), [])
