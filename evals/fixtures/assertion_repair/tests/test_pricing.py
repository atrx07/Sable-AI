import unittest

from pricing import total


class PricingTests(unittest.TestCase):
    def test_fee_is_added(self):
        self.assertEqual(total(20, 3), 23)


if __name__ == "__main__":
    unittest.main()
