# Changelog

User-visible changes are curated here. No historical release dates are inferred.
The current source version is 2.0.0; release approval and publication are separate.

## [Unreleased]

### Added

- Product-first README with source-install guidance, bounded safety claims,
  baseline-derived evaluation results, limitations, and documentation navigation.
- Maintainable Mermaid architecture, lifecycle, trust-boundary, verification,
  transaction, and evaluation diagrams grounded in the implemented components.
- Reproducible scripted showcase demos for repair, multi-file change, capability
  denial, bounded repair, conflict-aware undo, and machine automation.
- Unpublished 2.0.0 release-note draft, owner release-candidate checklist, and
  evidence-based repository/portfolio launch material.
- Packaging metadata, one derived version source, explicit developer/release extras,
  wheel/sdist content checks and clean installed-CLI smoke tests.
- Ruff lint/format policy, measured coverage floor, runtime dependency audit,
  production Bandit/CodeQL scans and weekly Dependabot checks.
- Contributor setup, issue/PR templates, private security-reporting guidance and
  explicit platform/support expectations.
- Manual, dry-run-default release validation with exact tag/version and prior-CI
  checks, SHA-256 integrity files, curated notes and separately gated GitHub/PyPI
  jobs. Publication requires external owner setup and explicit approval.
- Immutable current Action pins, Windows installed-wheel CI, downloaded-artifact
  checksum verification and a core-test matrix that avoids repeated eval suites.

### Fixed

- Fresh installations now use Groq's supported GPT-OSS 20B production model
  as the default fast model; explicit saved model choices remain unchanged.
- Environment-provided Groq keys are resolved at use time rather than copied into
  saved config during token accounting. Explicitly configured keys retain their
  existing plaintext-storage contract; config writes are now atomic and private
  from creation on POSIX.
- Key-slot display no longer includes credential prefixes/suffixes or short keys.
- The Termux installer keeps pip package-manager-owned, installs from its own
  source path, and rejects non-Termux hosts before any package operations.

### Existing v2 development foundation

- Bounded agent/runtime budgets and provider routing; workspace/capability checks
  with explicit native/PRoot execution guarantees.
- Reversible file-tool transactions, conflict-aware rollback, bounded sessions
  and redacted runtime traces.
- Repository context selection, deterministic verification and bounded repair
  with test-integrity safeguards.
- Interactive and single-task CLI, machine-readable task results, offline doctor
  and cancellation handling.
- Deterministic/adversarial/resilience evaluations, committed acceptance
  baseline and separately opt-in live evaluations.

These features reduce specific risks; they do not provide kernel isolation or
guarantee that arbitrary project code is safe. See [SECURITY.md](SECURITY.md).
