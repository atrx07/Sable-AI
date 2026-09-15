"""Fail-fast local release-confidence gate. Requires .[dev,release] and Bash."""

import shutil
import sys
from pathlib import Path

from scripts.release_check import ROOT, artifacts, run


def main():
    python = sys.executable
    for command in [
        [python, "-m", "compileall", "-q", "sable", "tests", "evals", "scripts", "sable.py"],
        [python, "-m", "ruff", "check", "."],
        [python, "-m", "ruff", "format", "--check", "."],
        [python, "-m", "unittest", "discover", "-s", "tests", "-v"],
        [python, "-m", "coverage", "run", "-m", "unittest", "discover", "-s", "tests"],
        [python, "-m", "coverage", "report"],
        [python, "-m", "bandit", "-r", "sable", "-ll"],
        [python, "-m", "pip_audit", "-r", "requirements.txt"],
        [
            python,
            "-m",
            "sable.evals",
            "--baseline",
            "evals/baselines/m7-deterministic.json",
            "--output",
            "evals/reports/generated/prepush",
        ],
    ]:
        run(command)
    git_bash = Path("C:/Program Files/Git/bin/bash.exe")
    bash = str(git_bash) if git_bash.is_file() else shutil.which("bash")
    if not bash:
        raise SystemExit("Bash is required for installer syntax validation")
    run([bash, "-n", "install.sh"])
    run([python, "-m", "build"])
    run([python, "-m", "twine", "check", *artifacts(ROOT / "dist")])
    run([python, "-m", "scripts.release_check", "--sdist"])
    run(["git", "diff", "--cached", "--check"])


if __name__ == "__main__":
    main()
