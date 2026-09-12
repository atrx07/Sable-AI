import unittest

from report import render_title


class ReportTests(unittest.TestCase):
    def test_title_is_normalized(self):
        self.assertEqual(render_title("  sable   report "), "Sable Report")


if __name__ == "__main__":
    unittest.main()
