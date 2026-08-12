import tempfile
import unittest
from pathlib import Path

from sable.project import ProjectInspector


class ProjectInspectorTests(unittest.TestCase):
    def test_detects_python_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "main.py").write_text("print('ok')\n")
            Path(tmp, "requirements.txt").write_text("fastapi\n")
            profile = ProjectInspector(tmp).profile()
            self.assertIn("Python", profile["languages"])
            self.assertEqual(profile["framework"], "FastAPI")
            commands = ProjectInspector(tmp).verification_commands()
            self.assertTrue(any(name == "Python syntax" for name, _ in commands))
