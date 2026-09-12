import unittest

from app.auth import authenticated


class AuthTests(unittest.TestCase):
    def test_valid_token(self):
        self.assertTrue(authenticated("valid"))


if __name__ == "__main__":
    unittest.main()
