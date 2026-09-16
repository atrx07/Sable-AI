# Development quality gates

Install into a virtual environment with `python -m pip install -e ".[dev,release]"`.
Run `python -m scripts.quality` from the checkout for the fail-fast local gate.
It compiles sources, checks Ruff lint/format, runs unittest and the committed
baseline, validates Bash installer syntax, builds wheel/sdist, runs Twine metadata
checks, and clean-installs both artifacts outside the source tree. Build and install
checks need package-index access; tests and deterministic evaluations do not.

Ruff checks Pyflakes, import order and syntax/style errors (E4/E7/E9). Formatting
uses Python 3.10 syntax and a 100-column target. `evals/fixtures` is excluded from
format/lint because canonical scenario inputs include exact patch targets and
intentional errors. Production eval implementation is included.

## Typing audit / deferred gate

An initial mypy 2.3.1 audit of all 66 production modules with Python 3.10 semantics
and `check_untyped_defs` reported 242 errors in 20 files on Windows. Most are
undeclared mixin host contracts, heterogeneous dictionaries, reused local variable
types and platform-specific stdlib APIs. A blocking type gate is **not adopted**
during the release-engineering audit: fixing this responsibly requires explicit protocol/annotation work, not
excluding runtime/security/verifier modules or globally suppressing diagnostics.
Lint and compile checks are not substitutes for type checking. This is a known
release-readiness limitation; revisit with a focused typing change.

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
files are omitted. The initial Windows/Python 3.14 measurement was 6749/8181
statements (82.50%); the non-decreasing floor is 82%, allowing a small platform
margin. `python -m coverage report` fails below that floor. Linux/Python 3.13 CI
also enforces it. Coverage is statement coverage of this process, not subprocess
or branch coverage. Do not lower the floor to accommodate a regression.

`python -m pip_audit -r requirements.txt` resolves the complete runtime dependency
graph. Keep that file aligned with project runtime metadata. This is not an audit
of unrelated development tools; the runtime-only audit has no advisory ignores.
Dependabot checks Python and Actions weekly; major upgrades stay separate and no
automatic merge is enabled.

`python -m bandit -r sable -ll` is a blocking medium/high-severity scan of all
production Python. The initial review found 66 low-severity diagnostics: assertion
invariants after explicit validation, enum/empty/synthetic secret strings, subprocess
imports and calls, PATH-resolved system tools, and an intentionally isolated event
observer exception. Low severity is informational, not a claim of zero findings;
review it with `python -m bandit -r sable`. Four medium findings have individual
`nosec` annotations: PRoot's in-root TMPDIR value (not host file creation), two
shell metadata/request carriers, and the intentional dispatch-gated raw-shell tool.
No scanner excludes a core module. Static analysis does not prove isolation or
prompt-injection immunity; the threat model in SECURITY.md remains authoritative.

CodeQL runs production Python security-extended queries on pushes, PRs and a weekly
schedule. Its SARIF upload requires `security-events: write` only in that job.
CI scan success indicates analysis completed, not necessarily zero CodeQL alerts;
review the repository Security surface as well.

The first CodeQL run found environment-derived API keys reaching plaintext
configuration writes. The persistence fix resolves environment keys at use time without
putting them in the saved dictionary, and uses atomic private config writes.
Regression tests cover token-usage saves, rotation, explicit key precedence,
failed replacement and POSIX permissions. Explicit `/keys` persistence is still
plaintext by design; see SECURITY.md for that limitation and migration guidance.
Follow-up analysis also flagged key prefix/suffix display, which could expose
short values entirely. Key-slot display now returns a constant configured/unset
marker without including credential characters; regression tests cover both
short and long values. No CodeQL finding is dismissed or query suppressed.

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

All external workflow actions are pinned to verified commits, with version comments
for review/Dependabot: checkout v7.0.1 (formerly v4), setup-python v7.0.0 (formerly v5),
upload-artifact v7.0.1, download-artifact v8.0.1, CodeQL v4, and PyPA release/v1.
The first-party JavaScript actions use their supported Node 24 line; no insecure
Node runtime override is set. Pins were resolved from official GitHub repositories
on 2026-09-15. Review both compatibility and release notes before accepting updates.

Ruff/coverage/security gates are not type safety, branch coverage, formal verification,
kernel isolation or proof of bit-for-bit reproducibility. No SBOM tool, hosted
coverage service, custom signing infrastructure or credential vault was added.
