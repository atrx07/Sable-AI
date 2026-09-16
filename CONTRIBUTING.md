# Contributing to Sable

Sable is a bounded local coding-agent runtime, not an OS sandbox. Read
[SECURITY.md](SECURITY.md) before changing execution, permissions, transactions,
verification, credential handling or model/tool boundaries.

## Set up a checkout

The supported CI matrix is Python 3.10–3.13. Use Python 3.13 for the full developer
toolchain. Git and Bash are required for the complete gate. On Linux/macOS:

```bash
git clone https://github.com/atrx07/Sable-AI.git
cd Sable-AI
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev,release]"
python -m scripts.quality
```

On Windows, create the environment with `py -3.13 -m venv .venv`, then use
`.venv\Scripts\python.exe` in place of `python`; activation is optional.
Install Git for Windows for Git/Bash. See [platforms](docs/platforms.md) for
Termux and tested-versus-expected support. Android may lack wheels for developer
tools; the runtime does not require those tools.

## Commands

From an activated environment in the repository root:

```bash
python -m compileall -q sable tests evals scripts sable.py
python -m unittest discover -s tests -v
python -m ruff check .
python -m ruff format --check .
python -m coverage run -m unittest discover -s tests
python -m coverage report
python -m bandit -r sable -ll
python -m pip_audit -r requirements.txt
python -m sable.evals --baseline evals/baselines/m7-deterministic.json --output evals/reports/generated/local
python -m build
python -m twine check dist/*
python -m scripts.release_check --sdist
bash -n install.sh
```

`python -m scripts.quality` is the authoritative fail-fast sequence, including
repository checks. Use `python -m ruff format .` to apply formatting and review
`python -m ruff check . --fix` changes before committing. On shells that do not
expand `dist/*`, use the full filenames or the canonical quality command.

The measured coverage floor is 82%. Do not lower it or the committed baseline to hide a
regression. A blocking type checker is deferred after an explicit whole-package
audit; see [quality policy](docs/quality.md) for evidence, scope and limitations.
Builds, vulnerability lookups and dependency installation need network access;
unit tests and deterministic evaluations need no real API key or provider access.

## Tests and evaluations

Add focused unittest regressions for behavior changes. Security-sensitive changes
should exercise allowed and denied paths, malformed/untrusted input, error handling
and resource/rollback boundaries. Avoid network calls and real credentials in tests.
Use clearly synthetic keys and isolated temporary workspaces/state directories.

For end-to-end cases, follow [evals/README.md](evals/README.md): add a bounded fixture
and a uniquely identified scenario, declare expected files/outcomes/metrics, and
exercise the real executor. Canonical fixtures intentionally include exact patch
targets and errors, so they are not autoformatted. Baseline changes need a specific
reviewed reason, never merely a failing run. Live evaluations require explicit
`--live`, are nondeterministic and may consume quota; they are not a contribution gate.

Update technical docs and [CHANGELOG.md](CHANGELOG.md) for user-visible changes.
Commit canonical scenarios, fixtures and baselines; never commit generated reports,
build outputs, local config, credentials, sessions, traces or virtual environments.

## Contribution workflow

External contributors should fork the repository, create a focused branch in
their fork, and submit a pull request with reproduction steps, tests and rationale.
Keep commits coherent and descriptive; separate mechanical formatting from behavior
where practical. Discuss large changes first. The owner's direct-`main` development
workflow is not an instruction for contributors to push to `main`.

Never use a public issue/PR for undisclosed vulnerability details; follow the
private-route guidance in [SECURITY.md](SECURITY.md). Be respectful and keep review
focused on the code. A formal Code of Conduct is deferred until the owner provides
a real private enforcement contact; no fictional contact is advertised.

## Version and release boundaries

The version literal lives in `sable/_version.py`. Package metadata and
`sable.__version__` derive from it. Use semantic-versioning expectations: MAJOR for
incompatible CLI/config/API behavior, MINOR for compatible features, PATCH for
bug/security/reliability fixes. The owner decides release numbers and timing.
The existing 2.0.0 source metadata is not proof of a published release.

A contribution does not authorize tags, GitHub Releases, PyPI uploads, secrets or
repository-setting changes. Keep release notes curated in the changelog and obtain
owner approval for publication. Repository automation only prepares and validates a release. Follow
[the release procedure](docs/releasing.md) for safe dry runs and owner setup.
