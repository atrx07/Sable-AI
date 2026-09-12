import unittest

from app.billing import invoice


class BillingTests(unittest.TestCase):
    def test_invoice(self):
        self.assertEqual(invoice(" A "), "a")


if __name__ == "__main__":
    unittest.main()
