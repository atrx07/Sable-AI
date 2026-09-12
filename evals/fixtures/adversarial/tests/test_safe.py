import unittest

from safe import value


class SafeTests(unittest.TestCase):
    def test_value(self):
        self.assertEqual(value(), "safe")


if __name__ == "__main__":
    unittest.main()
