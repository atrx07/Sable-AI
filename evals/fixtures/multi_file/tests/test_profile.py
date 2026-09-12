import unittest

from pkg.profile import profile_label


class ProfileTests(unittest.TestCase):
    def test_profile_label(self):
        self.assertEqual(profile_label(" ada ", " LOVELACE "), "Ada Lovelace")


if __name__ == "__main__":
    unittest.main()
