# Development quality gates

Install into a virtual environment with `python -m pip install -e ".[dev,release]"`.
Run `python -m scripts.quality` from the checkout for the fail-fast local gate.
The [gate implementation](../scripts/quality.py) compiles sources, checks Ruff
lint/format and repository metadata/links, runs unittest, coverage, Bandit, the
runtime dependency audit, and the committed baseline, validates Bash installer
syntax, builds wheel/sdist, checks Twine metadata, and clean-installs both artifacts
outside the source tree. It also validates release artifacts. Build/install checks
and vulnerability lookups need network access; tests and deterministic evaluations
do not.

Ruff checks Pyflakes, import order and syntax/style errors (E4/E7/E9). Formatting
uses Python 3.10 syntax and a 100-column target. `evals/fixtures` is excluded from
format/lint because canonical scenario inputs include exact patch targets and
intentional errors. Production eval implementation is included.

## Typing audit / deferred gate

Neither [the local gate](../scripts/quality.py) nor [CI](../.github/workflows/ci.yml)
enforces project-wide type checking. Mypy is not configured in
[pyproject.toml](../pyproject.toml). Do not present historical, uncommitted typing
diagnostic counts as a measurement of the current checkout. Adopting a blocking
gate needs a reproducible whole-package audit and focused protocol/annotation work,
not exclusions of runtime/security/verifier modules or blanket suppressions.
Lint and compilation are not substitutes for type checking.

## Distribution scope

`sable/_version.py` is authoritative; `sable.__version__` re-exports it and
setuptools reads the same literal without importing the runtime. The repository
retains the existing 2.0.0 source version and does not declare a release.
Wheel: production Python modules, metadata, entrypoint, MIT license. Sdist: also
source tests, canonical eval assets, developer scripts and technical docs.
The evaluation command requires the **source checkout** (or unpacked sdist), not
the runtime-only wheel. Normal installed `sable` commands require no checkout.
Generated reports, caches, session/config data and distributions are not source assets.

## Coverage and security

`python -m coverage run -m unittest discover -s tests` measures the entire `sable`
package, including eval implementation, CLI and platform backends. No production
files are omitted by the configuration. [The configured floor](../pyproject.toml)
is 82%; `python -m coverage report` fails below it. Linux/Python 3.13 CI also
enforces it. The floor is an acceptance requirement, not the current measured
percentage. Coverage is statement coverage of this process, not subprocess or
branch coverage. Record the actual report with its commit and host, and do not
lower the floor to accommodate a regression.

`python -m pip_audit -r requirements.txt` resolves the complete runtime dependency
graph. Keep that file aligned with project runtime metadata. This is not an audit
of unrelated development tools; the runtime-only audit has no advisory ignores.
[Dependabot](../.github/dependabot.yml) is configured to check Python and Actions
weekly, grouping minor/patch upgrades separately from major upgrades. Repository
configuration alone does not establish the current state of GitHub auto-merge settings.

`python -m bandit -r sable -ll` is a blocking medium/high-severity scan of all
production Python. Low-severity findings do not block this command; inspect them
with `python -m bandit -r sable` rather than interpreting a gate pass as zero
findings. Individual `nosec` annotations cover [PRoot's in-root TMPDIR](../sable/execution/proot.py),
[shell metadata/request carriers](../sable/tools/base.py), and the
[dispatch-gated raw-shell tool](../sable/tools/commands.py).
No scanner excludes a core module. Static analysis does not prove isolation or
prompt-injection immunity; the threat model in SECURITY.md remains authoritative.

CodeQL runs production Python security-extended queries on pushes, PRs and a weekly
schedule. Its SARIF upload requires `security-events: write` only in that job.
CI scan success indicates analysis completed, not necessarily zero CodeQL alerts;
review the repository Security surface as well.

[Configuration code](../sable/config.py) resolves environment keys at use time
without copying them into saved configuration and uses atomic private writes.
[Regression tests](../tests/test_config_persistence.py) cover token-usage saves,
rotation, explicit key precedence, failed replacement, POSIX permissions, and
key display that does not reveal credential characters. Explicit `/keys`
persistence is still plaintext by design; see [SECURITY.md](../SECURITY.md) for
that limitation and migration guidance. These code/test checks do not establish
the live repository's CodeQL alert or dismissal status.

## CI layout and limits

The Python 3.10–3.13 matrix runs core unit/integration tests using
`python -m scripts.run_tests --group core`. Evaluation test modules run once as
part of the complete coverage suite on Python 3.13, and the separate deterministic
job compares all 53 scenarios against the committed baseline. This avoids
running the expensive eval implementation tests four times. Local `scripts.quality`
still runs the entire unittest suite, coverage, and the baseline before each push.

The single Linux quality/package job handles lint, format, local links/YAML,
coverage, builds, metadata, installed wheel/sdist, and release integrity checks.
A separate security job handles the fully resolved runtime audit and Bandit.
Windows/Python 3.13 provides an additional build and installed-wheel smoke test;
it is not a duplicate full test matrix. CodeQL remains a separate production scan.
Release validation includes a read-only artifact download/hash verification job,
even on dry runs. All jobs have bounded timeouts. Caches contain only pip downloads,
keyed by `pyproject.toml`, never Sable state, secrets, traces or fixture workspaces.

External workflow actions use commit pins with version comments for review and
Dependabot. Inspect [CI](../.github/workflows/ci.yml),
[CodeQL](../.github/workflows/codeql.yml), and
[release validation](../.github/workflows/release.yml) for the exact pins used by
the checkout. Verify each pin against its upstream repository and review
compatibility/release notes before accepting an update; an older verification
date is not evidence for a changed pin.

Ruff/coverage/security gates are not type safety, branch coverage, formal verification,
kernel isolation or proof of bit-for-bit reproducibility. No SBOM tool, hosted
coverage service, custom signing infrastructure or credential vault was added.
