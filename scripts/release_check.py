"""Validate distributions and exercise real installs outside the source checkout.

No publishing, credentials, tag creation, or repository writes. Temporary virtual
environments are removed on exit. Installation/build isolation needs PyPI access.
"""

from __future__ import annotations

import argparse
import email
import os
import subprocess
import tarfile
import tempfile
import venv
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "sable-ai-agent"


def run(args, *, cwd=ROOT, env=None):
    print("+", " ".join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), cwd=cwd, env=env, check=True)


def artifacts(directory: Path) -> list[Path]:
    wheels = sorted(directory.glob("*.whl"))
    sources = sorted(directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        raise ValueError("Expected exactly one wheel and one sdist; use an empty output directory")
    return sorted(wheels + sources)


def validate_paths(names: list[str], *, wheel: bool) -> None:
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name:
            raise ValueError(f"Unsafe artifact path: {name}")
        parts = path.parts if wheel else path.parts[1:]
        if not parts:
            continue
        if any(
            part.startswith(".") or part in {"__pycache__", "generated", "traces"} for part in parts
        ) or path.suffix in {".pyc", ".pyo", ".bak"}:
            raise ValueError(f"Private/generated artifact content: {name}")
        if wheel:
            allowed = parts[0] == "sable" and path.suffix == ".py"
            allowed |= parts[0].endswith(".dist-info")
        else:
            allowed = parts[0] in {
                "sable",
                "tests",
                "scripts",
                "docs",
                "evals",
                "sable_ai_agent.egg-info",
                "LICENSE",
                "README.md",
                "SECURITY.md",
                "CONTRIBUTING.md",
                "CHANGELOG.md",
                "pyproject.toml",
                "requirements.txt",
                "install.sh",
                "sable.py",
                "MANIFEST.in",
                "PKG-INFO",
                "setup.cfg",
            }
            if parts[0] == "evals":
                allowed &= len(parts) > 1 and parts[1] in {
                    "fixtures",
                    "scenarios",
                    "baselines",
                    "README.md",
                }
        if not allowed:
            raise ValueError(f"Unexpected artifact content: {name}")


def inspect_artifacts(directory: Path) -> str:
    versions = set()
    expected = {p.relative_to(ROOT).as_posix() for p in (ROOT / "sable").rglob("*.py")}
    for artifact in artifacts(directory):
        if artifact.suffix == ".whl":
            with zipfile.ZipFile(artifact) as archive:
                names = archive.namelist()
                validate_paths(names, wheel=True)
                if not expected <= set(names):
                    raise ValueError("Wheel is missing production modules")
                metadata = archive.read(next(n for n in names if n.endswith("/METADATA")))
                entry = archive.read(next(n for n in names if n.endswith("/entry_points.txt")))
                if b"sable = sable.cli_app:main" not in entry:
                    raise ValueError("Missing console entry point")
                if not any(n.endswith("/licenses/LICENSE") for n in names):
                    raise ValueError("Wheel is missing MIT license")
        else:
            with tarfile.open(artifact) as archive:
                members = archive.getmembers()
                names = [m.name for m in members if m.isfile()]
                if any(not (m.isfile() or m.isdir()) for m in members):
                    raise ValueError("Unexpected sdist link or special file")
                validate_paths(names, wheel=False)
                relative = {n.split("/", 1)[1] for n in names}
                if not expected <= relative:
                    raise ValueError("Sdist is missing production modules")
                member = next(
                    m for m in members if m.name.count("/") == 1 and m.name.endswith("/PKG-INFO")
                )
                with archive.extractfile(member) as stream:
                    metadata = stream.read()
        msg = email.message_from_bytes(metadata)
        if (
            msg["Name"] != PROJECT
            or msg["Requires-Python"] != ">=3.10"
            or msg["License-Expression"] != "MIT"
        ):
            raise ValueError("Unexpected package metadata")
        versions.add(msg["Version"])
        print(f"Validated {artifact.name}: {len(names)} files")
    if len(versions) != 1:
        raise ValueError("Wheel and sdist versions differ")
    return versions.pop()


def smoke(artifact: Path, version: str) -> None:
    with tempfile.TemporaryDirectory(prefix="sable-install-") as temporary:
        root = Path(temporary)
        environment = root / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        binaries = environment / ("Scripts" if os.name == "nt" else "bin")
        python = binaries / ("python.exe" if os.name == "nt" else "python")
        sable = binaries / ("sable.exe" if os.name == "nt" else "sable")
        # No provider credentials/config or source-path leakage into the smoke process.
        env = {
            k: v
            for k, v in os.environ.items()
            if k.upper()
            in {
                "PATH",
                "SYSTEMROOT",
                "WINDIR",
                "COMSPEC",
                "PATHEXT",
                "TEMP",
                "TMP",
                "LANG",
            }
        }
        env.update(
            HOME=str(root), USERPROFILE=str(root), PYTHONUTF8="1", PIP_DISABLE_PIP_VERSION_CHECK="1"
        )
        run([python, "-m", "pip", "install", artifact.resolve()], cwd=root, env=env)
        code = (
            "import sable, importlib.metadata as m; from pathlib import Path; "
            f"assert sable.__version__ == m.version('{PROJECT}') == {version!r}; "
            f"assert Path(sable.__file__).is_relative_to(Path({str(environment)!r})); "
            "print('installed import/version OK:', sable.__version__)"
        )
        run([python, "-I", "-c", code], cwd=root, env=env)
        result = subprocess.run(
            [str(sable), "--version"], cwd=root, env=env, capture_output=True, text=True, check=True
        )
        if version not in result.stdout:
            raise ValueError("CLI version mismatch")
        print(result.stdout.strip())
        run([sable, "--help"], cwd=root, env=env)
        fixture = root / "workspace"
        fixture.mkdir()
        result = subprocess.run(
            [str(sable), "doctor", str(fixture)],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
        )
        if result.returncode != 2 or "Mode: offline" not in result.stdout:
            raise ValueError(
                f"Unexpected credential-free doctor contract: {result.returncode}, {result.stdout}, {result.stderr}"
            )
        if list(fixture.iterdir()):
            raise ValueError("Doctor mutated the workspace")
        print("installed doctor: offline, missing-config exit 2, workspace unchanged")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--sdist", action="store_true", help="also clean-install the sdist")
    args = parser.parse_args()
    version = inspect_artifacts(args.dist)
    for artifact in artifacts(args.dist):
        if artifact.suffix == ".whl" or args.sdist:
            smoke(artifact, version)


if __name__ == "__main__":
    main()
