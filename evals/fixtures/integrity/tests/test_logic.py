import unittest

from logic import positive


class LogicTests(unittest.TestCase):
    def test_positive(self):
        self.assertTrue(positive(2))

    def test_zero(self):
        self.assertFalse(positive(0))


if __name__ == "__main__":
    unittest.main()
