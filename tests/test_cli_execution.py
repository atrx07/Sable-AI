import io
import tempfile
import unittest
from contextlib import redirect_stdout

from sable.cli_workspace import WorkspaceCommandsMixin
from sable.tools import ToolExecutor


class SandboxCommandHarness(WorkspaceCommandsMixin):
    def __init__(self, executor):
        self.executor = executor


class SandboxCommandTests(unittest.TestCase):
    def test_status_is_truthful_about_native_non_isolation(self):
        with tempfile.TemporaryDirectory() as root:
            stream = io.StringIO()
            with redirect_stdout(stream):
                SandboxCommandHarness(ToolExecutor(root, execution_backend="native"))._cmd_sandbox()
            output = stream.getvalue()
            self.assertIn("Backend      native", output)
            self.assertIn("Network isolation    NOT_SUPPORTED", output)
            self.assertIn("Process isolation    NOT_SUPPORTED", output)
            self.assertIn("Private HOME         ENFORCED", output)


if __name__ == "__main__":
    unittest.main()
