"""Build and independently exercise the immutable consumer release artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from local_agent_runtime.api_contract import API_VERSION

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "clients" / "typescript"
DIST = ROOT / "dist"
MANIFEST = "SHA256SUMS"
FORBIDDEN_MEMBER_PARTS = {
    ".env",
    ".runtime-state",
    "node_modules",
    "runtime.local.yaml",
}
TOKEN_PATTERNS = (
    re.compile(rb"(?:sk|sk-ant)-[A-Za-z0-9_-]{20,}"),
    re.compile(rb"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(rb"(?i)Bearer[ \t]+[A-Za-z0-9._~+/=-]{24,}"),
)
SECRET_ASSIGNMENT = re.compile(
    rb"(?im)\b(?:OPENROUTER_API_KEY|ANTHROPIC_API_KEY|XAI_API_KEY)[ \t]*=[ \t]*([^\s\"']+)"
)
PLACEHOLDER_PREFIXES = (b"replace-", b"example", b"test", b"[REDACTED]", b"${")
VERSION_PATTERN = r"[0-9]+\.[0-9]+\.[0-9]+(?:[A-Za-z0-9.-]+)?"


class ReleaseError(RuntimeError):
    """A stable release-integrity failure."""


def run(command: list[str], *, cwd: Path = ROOT, capture: bool = False) -> str:
    print(f"+ {' '.join(command)}", flush=True)
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=True,
        text=True,
        capture_output=capture,
    )
    return completed.stdout


def require_clean() -> None:
    status = run(["git", "status", "--porcelain", "--untracked-files=all"], capture=True).strip()
    if status:
        raise ReleaseError("Release builds require a clean Git working tree")


def release_version() -> str:
    try:
        python_version = str(
            tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
        )
        package = json.loads((CLIENT / "package.json").read_text())
        lock = json.loads((CLIENT / "package-lock.json").read_text())
        version_source = (ROOT / "src/local_agent_runtime/version.py").read_text()
        contract = json.loads((ROOT / "contracts/openapi.json").read_text())
    except (KeyError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ReleaseError("Release metadata is malformed") from exc
    match = re.search(r'^PACKAGE_VERSION = "([^"]+)"$', version_source, re.MULTILINE)
    source_version = match.group(1) if match else None
    versions = {
        "pyproject": python_version,
        "python export": source_version,
        "TypeScript package": package.get("version"),
        "TypeScript lock": lock.get("version"),
    }
    if any(value != python_version for value in versions.values()):
        raise ReleaseError(f"Release version mismatch: {versions}")
    if API_VERSION != "1.0.0":
        raise ReleaseError("The initial release requires gateway API version 1.0.0")
    if contract.get("info", {}).get("version") != API_VERSION:
        raise ReleaseError("Committed OpenAPI version does not match the runtime API version")
    return python_version


def expected_names(version: str) -> set[str]:
    return {
        f"local_agent_runtime-{version}-py3-none-any.whl",
        f"local_agent_runtime-{version}.tar.gz",
        f"local-agent-runtime-client-{version}.tgz",
    }


def _safe_members(names: Iterable[str]) -> None:
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise ReleaseError(f"Artifact contains an unsafe path: {name}")
        if any(part in FORBIDDEN_MEMBER_PARTS for part in path.parts):
            raise ReleaseError(f"Artifact contains a forbidden path: {name}")


def _safe_content(name: str, content: bytes) -> None:
    local_paths = {str(Path.home()).encode(), str(ROOT.parent).encode()}
    for marker in local_paths:
        if marker and marker in content:
            raise ReleaseError(f"Artifact contains a developer-local path: {name}")
    for pattern in TOKEN_PATTERNS:
        if pattern.search(content):
            raise ReleaseError(f"Artifact contains a credential-shaped value: {name}")
    for match in SECRET_ASSIGNMENT.finditer(content):
        value = match.group(1)
        if not value.startswith(PLACEHOLDER_PREFIXES):
            raise ReleaseError(f"Artifact contains a populated secret assignment: {name}")


def inspect_sdist_members(path: Path, version: str) -> None:
    root = f"local_agent_runtime-{version}"
    allowed_files = {
        f"{root}/.gitignore",
        f"{root}/LICENSE",
        f"{root}/README.md",
        f"{root}/pyproject.toml",
        f"{root}/PKG-INFO",
    }
    with tarfile.open(path, "r:gz") as archive:
        files = {item.name for item in archive.getmembers() if item.isfile()}
    unexpected = {
        name
        for name in files
        if name not in allowed_files and not name.startswith(f"{root}/src/local_agent_runtime/")
    }
    missing = allowed_files - files
    if unexpected or missing:
        raise ReleaseError(
            "Source archive membership is invalid: "
            f"unexpected={sorted(unexpected)}, missing={sorted(missing)}"
        )


def inspect_license_files(paths: Iterable[Path], version: str) -> None:
    expected = (ROOT / "LICENSE").read_bytes()
    locations = {
        f"local_agent_runtime-{version}-py3-none-any.whl": (
            "zip",
            f"local_agent_runtime-{version}.dist-info/licenses/LICENSE",
        ),
        f"local_agent_runtime-{version}.tar.gz": (
            "tar",
            f"local_agent_runtime-{version}/LICENSE",
        ),
        f"local-agent-runtime-client-{version}.tgz": ("tar", "package/LICENSE"),
    }
    for path in paths:
        archive_kind, member = locations[path.name]
        try:
            if archive_kind == "zip":
                with zipfile.ZipFile(path) as archive:
                    actual = archive.read(member)
            else:
                with tarfile.open(path, "r:gz") as archive:
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        raise KeyError(member)
                    actual = extracted.read()
        except KeyError as exc:
            raise ReleaseError(f"Artifact license is missing: {path.name}") from exc
        if actual != expected:
            raise ReleaseError(f"Artifact license does not match repository license: {path.name}")


def inspect_archive(path: Path) -> None:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            _safe_members(archive.namelist())
            for zip_item in archive.infolist():
                if not zip_item.is_dir():
                    _safe_content(zip_item.filename, archive.read(zip_item))
        return
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        _safe_members(tar_item.name for tar_item in members)
        for tar_item in members:
            if tar_item.isfile():
                extracted = archive.extractfile(tar_item)
                if extracted is not None:
                    _safe_content(tar_item.name, extracted.read())


def source_commit() -> str:
    commit = run(["git", "rev-parse", "HEAD"], capture=True).strip()
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ReleaseError("Unable to establish the release source commit")
    return commit


def write_manifest(paths: Iterable[Path], destination: Path, *, version: str, commit: str) -> None:
    lines = [f"# local-agent-runtime version={version} source={commit}\n"]
    for path in sorted(paths, key=lambda item: item.name):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.name}\n")
    destination.write_text("".join(lines), encoding="utf-8")


def manifest_identity(lines: list[str]) -> tuple[str, str]:
    if not lines:
        raise ReleaseError("Release checksum manifest is empty")
    match = re.fullmatch(
        rf"# local-agent-runtime version=({VERSION_PATTERN}) source=([0-9a-f]{{40}})", lines[0]
    )
    if match is None:
        raise ReleaseError("Release checksum manifest identity is malformed")
    return match.group(1), match.group(2)


def verify_manifest(directory: Path, expected: set[str] | None = None) -> tuple[str, str]:
    manifest = directory / MANIFEST
    if not manifest.is_file():
        raise ReleaseError("Release checksum manifest is missing")
    lines = manifest.read_text(encoding="utf-8").splitlines()
    version, commit = manifest_identity(lines)
    expected = expected or expected_names(version)
    found: set[str] = set()
    for line in lines[1:]:
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.-]+)", line)
        if match is None:
            raise ReleaseError("Release checksum manifest is malformed")
        digest, name = match.groups()
        if name in found:
            raise ReleaseError("Release checksum manifest contains a duplicate")
        path = directory / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ReleaseError(f"Release checksum mismatch: {name}")
        found.add(name)
    if found != expected:
        raise ReleaseError(
            f"Release artifact set mismatch: expected={sorted(expected)}, found={sorted(found)}"
        )
    actual = {path.name for path in directory.iterdir() if path.is_file()}
    if actual != expected | {MANIFEST}:
        raise ReleaseError(f"Release directory contains unexpected files: {sorted(actual)}")
    return version, commit


def check_typescript_tarball(tarball: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="lar-client-") as raw_directory:
        directory = Path(raw_directory)
        run(
            ["npm", "install", "--ignore-scripts", "--no-package-lock", "--no-save", str(tarball)],
            cwd=directory,
        )
        run(
            [
                "node",
                "--input-type=module",
                "--eval",
                "import { RuntimeClient } from '@local-agent-runtime/client'; "
                "if (typeof RuntimeClient !== 'function' || "
                "typeof RuntimeClient.prototype.health !== 'function' || "
                "typeof RuntimeClient.prototype.createSession !== 'function') process.exit(1);",
            ],
            cwd=directory,
        )
    print("Isolated TypeScript tarball import passed.")


def check_sdist(sdist: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="lar-sdist-") as raw_directory:
        directory = Path(raw_directory)
        run(["uv", "build", "--wheel", "--no-sources", "--out-dir", str(directory), str(sdist)])
        wheels = list(directory.glob("local_agent_runtime-*-py3-none-any.whl"))
        if len(wheels) != 1:
            raise ReleaseError("The source distribution did not rebuild one wheel")
    print("Isolated source distribution rebuild passed.")


def finalize_manifest(
    artifacts: Iterable[Path],
    destination: Path,
    *,
    version: str,
    commit: str,
    release_eligible: bool,
) -> None:
    manifest = destination / MANIFEST
    if not release_eligible:
        manifest.unlink(missing_ok=True)
        print("Development artifacts passed; no release manifest was created.")
        return
    write_manifest(artifacts, manifest, version=version, commit=commit)
    verify_manifest(destination, expected_names(version))


def build(*, allow_dirty: bool) -> None:
    if not allow_dirty:
        require_clean()
    version = release_version()
    commit = source_commit()
    run(["uv", "run", "python", "scripts/generate_contracts.py", "--check"])
    run(["npm", "--prefix", "clients/typescript", "run", "build"])
    run(["uv", "run", "python", "scripts/check_typescript_package.py"])
    shutil.rmtree(DIST, ignore_errors=True)
    DIST.mkdir()
    run(
        [
            "uv",
            "build",
            "--no-sources",
            "--no-create-gitignore",
            "--out-dir",
            str(DIST),
        ]
    )
    try:
        packed = json.loads(
            run(
                ["npm", "pack", "--ignore-scripts", "--json", "--pack-destination", str(DIST)],
                cwd=CLIENT,
                capture=True,
            )
        )
    except json.JSONDecodeError as exc:
        raise ReleaseError("npm returned an invalid pack result") from exc
    if not isinstance(packed, list) or len(packed) != 1:
        raise ReleaseError("npm produced an unexpected artifact set")
    names = {path.name for path in DIST.iterdir() if path.is_file()}
    expected = expected_names(version)
    if names != expected:
        raise ReleaseError(
            f"Built artifact set mismatch: expected={sorted(expected)}, found={sorted(names)}"
        )
    artifacts = [DIST / name for name in expected]
    for artifact in artifacts:
        inspect_archive(artifact)
    inspect_license_files(artifacts, version)
    inspect_sdist_members(DIST / f"local_agent_runtime-{version}.tar.gz", version)
    wheel = DIST / f"local_agent_runtime-{version}-py3-none-any.whl"
    sdist = DIST / f"local_agent_runtime-{version}.tar.gz"
    tarball = DIST / f"local-agent-runtime-client-{version}.tgz"
    run(["uv", "run", "python", "scripts/check_install.py", "--wheel", str(wheel)])
    check_sdist(sdist)
    check_typescript_tarball(tarball)
    finalize_manifest(
        artifacts,
        DIST,
        version=version,
        commit=commit,
        release_eligible=not allow_dirty,
    )
    if not allow_dirty:
        print(f"Release v{version} from {commit} ready in {DIST}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="permit a development build; never use for uploaded release artifacts",
    )
    parser.add_argument("--verify", type=Path, help="verify an existing release download directory")
    args = parser.parse_args()
    try:
        if args.verify is not None:
            version, commit = verify_manifest(args.verify.resolve())
            print(f"Release v{version} from {commit} artifact checksums passed.")
        else:
            build(allow_dirty=args.allow_dirty)
    except (
        OSError,
        ReleaseError,
        subprocess.CalledProcessError,
        tarfile.TarError,
        zipfile.BadZipFile,
    ) as exc:
        raise SystemExit(str(exc)) from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
