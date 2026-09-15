"""Run core tests in the version matrix; full/eval tests remain explicit gates."""

import argparse
import unittest
from pathlib import Path


def select_tests(suite, group):
    selected = unittest.TestSuite()
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            selected.addTests(select_tests(test, group))
        else:
            is_eval = test.id().split(".", 1)[0].startswith("test_evals_")
            if group == "all" or (group == "evals") == is_eval:
                selected.addTest(test)
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", choices=("all", "core", "evals"), default="all")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    suite = unittest.TestLoader().discover(str(root / "tests"))
    selected = select_tests(suite, args.group)
    if not selected.countTestCases():
        raise SystemExit("No tests selected")
    raise SystemExit(0 if unittest.TextTestRunner(verbosity=2).run(selected).wasSuccessful() else 1)


if __name__ == "__main__":
    main()
