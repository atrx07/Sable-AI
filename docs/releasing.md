# Release procedure

M8 prepares release engineering; it does **not** authorize publication. No release
tag, GitHub Release or PyPI upload is created by normal `main` pushes or tag pushes.
The `Release validation` workflow is manual-dispatch only, with both publication
inputs defaulting to false. Build checks use ordinary read permissions; only the
separate approved GitHub Release job receives `contents: write`, and only the PyPI
job receives `id-token: write`.

## Safe dry run

From a source checkout with `.[dev,release]` installed, run:

```bash
python -m scripts.quality
python -m scripts.release --tag v2.0.0
```

Substitute the proposed version. This validates a tag string but never creates it.
The full gate runs tests, coverage, lint/format, repository checks, dependency and
static scans, the M7 baseline, builds, Twine, and clean wheel/sdist installs. It
then inspects artifacts and generates `dist/SHA256SUMS.txt` in filename order and
`dist/RELEASE_NOTES.md` from the curated Unreleased section. Generated outputs are
ignored. Use an empty `dist` directory when changing versions; stale extra wheel
or sdist files are rejected, not silently selected or deleted.

For a CI dry run, manually dispatch `release.yml` on `main`, leaving both inputs
false. Its validated artifacts are retained for 14 days. A read-only job downloads
and verifies the artifact hashes even during dry runs; both publication jobs depend
on that check. Both publication jobs
must be **skipped**. This is not a production release. Checksums establish integrity
of those specific artifacts, not bit-for-bit build reproducibility. Build and
runtime dependencies resolve within declared bounds rather than a frozen lock.

## OWNER SETUP REQUIRED before any publication

1. Review all gates and open security findings. Configure GitHub environments
   named `release` and `pypi` with required reviewers and tag restrictions (`v*`),
   using available repository-plan controls. Do not enable publication variables
   until the desired approval protections are in place.
2. Confirm ownership/availability of the PyPI project name `sable-ai-agent`. Configure
   a normal or pending GitHub trusted publisher in PyPI: owner `atrx07`, repository
   `Sable-AI`, workflow filename `release.yml`, environment `pypi`. This external
   association is not created by repository code. Do not add an API-token secret.
3. Set repository Actions variable `SABLE_RELEASE_ENABLED=true` only when GitHub
   Releases are approved, and `SABLE_PYPI_ENABLED=true` only when PyPI setup and
   publication are approved. Leaving them absent disables publication. Requesting
   publication without enablement fails validation with OWNER SETUP REQUIRED.
4. Choose the release version, update only `sable/_version.py`, and move approved
   changes into a non-empty `## [VERSION] - YYYY-MM-DD` changelog section using the
   actual release date. Keep an Unreleased section for subsequent development.
   Commit on the owner's authorized workflow and require CI and CodeQL to pass.
5. Only with explicit owner approval, create the corresponding existing `vVERSION`
   tag on the validated main commit. Dispatch the workflow on that tag and explicitly
   select the desired publication inputs; approve the protected environments.

Before publishing, validation checks tag equals built version, tag commit is an
ancestor of `origin/main`, successful **main/push** CI and CodeQL runs exist for
that exact SHA, no high/critical CodeQL alerts are open, and a curated versioned
changelog section exists. It reruns the complete local gate in CI before upload.
No ordinary push invokes these publication jobs.

The GitHub job downloads the validated wheel, sdist and checksum file, verifies
the hashes, and uses `gh release create --verify-tag`; it does not invent a tag or
overwrite an existing release. The PyPI job separately verifies and selects only
wheel/sdist, then uses the official PyPA OIDC action. Either publication can fail
independently: inspect actual registry/release state before rerunning; do not bypass
duplicate-version or checksum errors. No `skip-existing` or blanket error suppression
is used.

The PyPA action's attestation option is enabled for future trusted publication;
M8 does not claim artifacts have already been signed/attested. TestPyPI and SBOM
generation are optional and not required by this pipeline. There is no custom signing
service or long-lived token. See [PyPI trusted publishing](https://docs.pypi.org/trusted-publishers/using-a-publisher/)
and the [official action](https://github.com/pypa/gh-action-pypi-publish).

Private vulnerability reporting is separately disabled until the owner enables
it; see [SECURITY.md](../SECURITY.md). A formal Code of Conduct awaits a real private
enforcement contact. These owner actions are not performed by the release helper.
