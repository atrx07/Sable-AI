import unittest

from parser import parse_count


class ParserTests(unittest.TestCase):
    def test_parses_integer(self):
        self.assertEqual(parse_count("4"), 4)


if __name__ == "__main__":
    unittest.main()
