"""Offline checks for local Markdown targets and repository YAML/metadata."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def workflow_errors(value: dict, name: str) -> list[str]:
    errors = []
    if value.get("permissions") != {"contents": "read"}:
        errors.append(f"{name}: workflow-level permissions must remain contents: read")
    for job_id, job in value.get("jobs", {}).items():
        if str(job.get("continue-on-error", "false")).lower() == "true":
            errors.append(f"{name}/{job_id}: mandatory job cannot ignore failures")
        for permission, level in job.get("permissions", {}).items():
            allowed = {
                ("release.yml", "github-release", "contents"),
                ("release.yml", "pypi", "id-token"),
                ("codeql.yml", "analyze", "security-events"),
            }
            if level == "write" and (name, job_id, permission) not in allowed:
                errors.append(f"{name}/{job_id}: excessive {permission} write permission")
        for step in job.get("steps", []):
            if "uses" in step and not re.fullmatch(r"[^@]+@[0-9a-f]{40}", step["uses"]):
                errors.append(f"{name}/{job_id}: action must use an immutable commit pin")
            if str(step.get("continue-on-error", "false")).lower() == "true":
                errors.append(f"{name}/{job_id}: mandatory step cannot ignore failures")
    if name == "release.yml":
        triggers = value.get("on", {})
        if set(triggers) != {"workflow_dispatch"}:
            errors.append("Release workflow must remain manual-dispatch only")
        inputs = (triggers.get("workflow_dispatch") or {}).get("inputs", {})
        for target, job_id, variable in (
            ("github", "github-release", "SABLE_RELEASE_ENABLED"),
            ("pypi", "pypi", "SABLE_PYPI_ENABLED"),
        ):
            if str(inputs.get(f"publish_{target}", {}).get("default")).lower() != "false":
                errors.append(f"Release {target} publication must default to false")
            job = value.get("jobs", {}).get(job_id, {})
            guard = job.get("if", "")
            if job.get("needs") != "verify-artifact" or not all(
                part in guard
                for part in (
                    "workflow_dispatch",
                    f"inputs.publish_{target}",
                    "refs/tags/v",
                    variable,
                )
            ):
                errors.append(f"Release {target} publication guards are missing")
    return errors


def broken_links(document: Path, root: Path) -> list[str]:
    text = re.sub(r"```.*?```", "", document.read_text(encoding="utf-8"), flags=re.S)
    errors = []
    for target in re.findall(r"!?\[[^\]\n]*\]\(([^)\n]+)\)", text):
        target = target.strip().split(' "', 1)[0].strip("<>")
        link = urlsplit(target)
        if link.scheme or link.netloc or not link.path:
            continue
        resolved = (document.parent / unquote(link.path)).resolve()
        if not resolved.is_relative_to(root.resolve()) or not resolved.exists():
            errors.append(f"{document.relative_to(root)}: invalid local target {target}")
    return errors


def main():
    import yaml

    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib

    required = [
        "README.md",
        "SECURITY.md",
        "CONTRIBUTING.md",
        "CHANGELOG.md",
        "SUPPORT.md",
        "LICENSE",
        "docs/architecture.md",
        "docs/demo.md",
        ".github/ISSUE_TEMPLATE/bug.yml",
        ".github/ISSUE_TEMPLATE/feature.yml",
        ".github/ISSUE_TEMPLATE/config.yml",
        ".github/pull_request_template.md",
        ".github/dependabot.yml",
    ]
    errors = [f"Missing {name}" for name in required if not (ROOT / name).is_file()]
    documents = [*ROOT.glob("*.md"), *(ROOT / "docs").rglob("*.md"), ROOT / "evals/README.md"]
    for document in documents:
        errors.extend(broken_links(document, ROOT))
    yamls = list((ROOT / ".github").rglob("*.yml"))
    for path in yamls:
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        if not isinstance(value, dict):
            errors.append(f"Expected YAML mapping: {path.relative_to(ROOT)}")
        elif path.parent.name == "workflows":
            errors.extend(workflow_errors(value, path.name))
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = [
        line.strip()
        for line in (ROOT / "requirements.txt").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if sorted(requirements) != sorted(metadata["project"]["dependencies"]):
        errors.append("requirements.txt must match project runtime dependencies")
    if errors:
        raise SystemExit("\n".join(errors))
    print(
        f"Repository checks passed: {len(documents)} Markdown files, {len(yamls)} YAML files, runtime metadata"
    )


if __name__ == "__main__":
    main()
