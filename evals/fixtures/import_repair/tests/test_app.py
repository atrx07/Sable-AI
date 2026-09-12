import unittest

from app import greeting


class AppTests(unittest.TestCase):
    def test_greeting(self):
        self.assertEqual(greeting(" sable "), "Hello, Sable")


if __name__ == "__main__":
    unittest.main()
