import unittest

from app.users import username


class UserTests(unittest.TestCase):
    def test_username(self):
        self.assertEqual(username(" Ada "), "ada")


if __name__ == "__main__":
    unittest.main()
