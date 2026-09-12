import unittest

from calculator import add


class CalculatorTests(unittest.TestCase):
    def test_adds_positive_numbers(self):
        self.assertEqual(add(7, 5), 12)


if __name__ == "__main__":
    unittest.main()
