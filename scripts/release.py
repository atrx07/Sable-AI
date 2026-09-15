"""Prepare release integrity files; publication itself is confined to gated CI jobs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from scripts.release_check import ROOT, artifacts, inspect_artifacts


def validate_tag(tag: str, version: str) -> None:
    if tag != f"v{version}":
        raise ValueError(f"Release tag {tag!r} does not match package version {version!r}")


def checksum_text(directory: Path) -> str:
    lines = []
    for path in artifacts(directory):
        if path.is_symlink() or not path.is_file():
            raise ValueError("Release artifacts must be regular files")
        lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n")
    return "".join(lines)


def verify_checksums(directory: Path) -> None:
    if (directory / "SHA256SUMS.txt").read_text(encoding="utf-8") != checksum_text(directory):
        raise ValueError("Artifact checksum list/content mismatch")


def release_notes(text: str, version: str, *, publishing: bool) -> str:
    sections = re.split(r"(?m)^## \[([^\]]+)\][^\n]*\n", text)
    wanted = [version] if publishing else ["Unreleased", version]
    for label in wanted:
        for index in range(1, len(sections), 2):
            if sections[index] == label and sections[index + 1].strip():
                return f"# Sable {version}\n\n{sections[index + 1].strip()}\n"
    raise ValueError(f"A curated non-empty changelog section is required: {wanted}")


def publication_requested(env: dict[str, str]) -> bool:
    github = env.get("PUBLISH_GITHUB") == "true"
    pypi = env.get("PUBLISH_PYPI") == "true"
    if not (github or pypi):
        return False
    if env.get("GITHUB_EVENT_NAME") != "workflow_dispatch" or not env.get(
        "GITHUB_REF", ""
    ).startswith("refs/tags/v"):
        raise ValueError("Publication requires a deliberate manual dispatch on an existing v* tag")
    if github and env.get("SABLE_RELEASE_ENABLED") != "true":
        raise ValueError("OWNER SETUP REQUIRED: SABLE_RELEASE_ENABLED")
    if pypi and env.get("SABLE_PYPI_ENABLED") != "true":
        raise ValueError("OWNER SETUP REQUIRED: SABLE_PYPI_ENABLED and PyPI trusted publisher")
    return True


def require_successful_run(runs: list[dict], sha: str) -> None:
    if not any(r.get("headSha") == sha and r.get("conclusion") == "success" for r in runs):
        raise ValueError(f"No successful required main/push workflow for exact commit {sha}")


def check_remote_release_gate(env: dict[str, str]) -> None:
    sha = env["GITHUB_SHA"]
    repository = env["GITHUB_REPOSITORY"]
    subprocess.run(["git", "merge-base", "--is-ancestor", sha, "origin/main"], cwd=ROOT, check=True)
    for workflow in ("ci.yml", "codeql.yml"):
        result = subprocess.run(
            [
                "gh",
                "run",
                "list",
                "--repo",
                repository,
                "--workflow",
                workflow,
                "--branch",
                "main",
                "--event",
                "push",
                "--commit",
                sha,
                "--json",
                "headSha,conclusion",
                "--limit",
                "100",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        require_successful_run(json.loads(result.stdout), sha)
    result = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{repository}/code-scanning/alerts?state=open&per_page=100",
            "--paginate",
            "--slurp",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    alerts = [item for page in json.loads(result.stdout) for item in page]
    if any(
        a.get("rule", {}).get("security_severity_level") in {"critical", "high"} for a in alerts
    ):
        raise ValueError("Resolve open high/critical CodeQL alerts before publication")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--tag", help="validate a proposed tag without creating one")
    parser.add_argument("--publication-check", action="store_true")
    args = parser.parse_args()
    publishing = publication_requested(dict(os.environ)) if args.publication_check else False
    version = inspect_artifacts(args.dist)
    tag = args.tag
    if args.publication_check and os.environ.get("GITHUB_REF", "").startswith("refs/tags/"):
        tag = os.environ["GITHUB_REF"].removeprefix("refs/tags/")
    if tag is not None:
        validate_tag(tag, version)
    if publishing:
        check_remote_release_gate(dict(os.environ))
    notes = release_notes(
        (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), version, publishing=publishing
    )
    (args.dist / "SHA256SUMS.txt").write_text(
        checksum_text(args.dist), encoding="utf-8", newline="\n"
    )
    (args.dist / "RELEASE_NOTES.md").write_text(notes, encoding="utf-8", newline="\n")
    verify_checksums(args.dist)
    print(
        f"Release integrity checks passed for {version}; publishing checks={'enabled' if publishing else 'dry-run'}"
    )
    print("No tag, GitHub Release or registry upload is performed by this helper.")


if __name__ == "__main__":
    main()
