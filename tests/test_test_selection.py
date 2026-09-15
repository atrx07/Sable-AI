import unittest

from scripts.run_tests import select_tests


class TestSelectionTests(unittest.TestCase):
    def test_core_and_eval_groups_are_disjoint_and_complete(self):
        class Case(unittest.TestCase):
            def __init__(self, identifier):
                super().__init__()
                self.identifier = identifier

            def id(self):
                return self.identifier

        tests = [Case("test_evals_system.Case.test_one"), Case("test_security.Case.test_two")]
        suite = unittest.TestSuite([unittest.TestSuite(tests)])
        core = list(select_tests(suite, "core"))
        evals = list(select_tests(suite, "evals"))
        self.assertEqual([test.id() for test in core], [tests[1].id()])
        self.assertEqual([test.id() for test in evals], [tests[0].id()])
        self.assertEqual(
            {test.id() for test in select_tests(suite, "all")}, {test.id() for test in core + evals}
        )
