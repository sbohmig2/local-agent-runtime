from __future__ import annotations

import hashlib
import io
import json
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import build_release
from scripts.build_release import (
    ReleaseError,
    finalize_manifest,
    inspect_archive,
    inspect_license_files,
    inspect_python_typing_marker,
    inspect_sdist_members,
    release_version,
    require_clean,
    source_commit,
    verify_manifest,
    write_manifest,
)

VERSION = "0.1.2"
COMMIT = "1" * 40


def manifest(paths: list[Path], directory: Path) -> None:
    write_manifest(paths, directory / "SHA256SUMS", version=VERSION, commit=COMMIT)


def test_manifest_is_sorted_and_verifies_without_checkout_version(tmp_path: Path) -> None:
    names = build_release.expected_names(VERSION)
    artifacts = []
    for name in reversed(sorted(names)):
        path = tmp_path / name
        path.write_bytes(name.encode())
        artifacts.append(path)

    manifest(artifacts, tmp_path)

    lines = (tmp_path / "SHA256SUMS").read_text().splitlines()
    assert lines[0] == f"# local-agent-runtime version={VERSION} source={COMMIT}"
    assert [line.split("  ")[1] for line in lines[1:]] == sorted(names)
    assert verify_manifest(tmp_path) == (VERSION, COMMIT)


def test_manifest_rejects_changed_or_unexpected_artifacts(tmp_path: Path) -> None:
    artifact = tmp_path / "package.whl"
    artifact.write_bytes(b"original")
    manifest([artifact], tmp_path)
    artifact.write_bytes(b"changed")

    with pytest.raises(ReleaseError, match="checksum mismatch"):
        verify_manifest(tmp_path, {"package.whl"})

    artifact.write_bytes(b"original")
    with pytest.raises(ReleaseError, match="artifact set mismatch"):
        verify_manifest(tmp_path, {"package.whl", "missing.tgz"})


@pytest.mark.parametrize(
    "lines,match",
    [
        (["not an identity"], "identity is malformed"),
        (
            [
                f"# local-agent-runtime version={VERSION} source={COMMIT}",
                f"{hashlib.sha256(b'original').hexdigest()}  package.whl",
                f"{hashlib.sha256(b'original').hexdigest()}  package.whl",
            ],
            "duplicate",
        ),
        (
            [f"# local-agent-runtime version={VERSION} source={COMMIT}", "malformed"],
            "manifest is malformed",
        ),
    ],
)
def test_manifest_rejects_malformed_identity_lines_and_duplicates(
    tmp_path: Path, lines: list[str], match: str
) -> None:
    (tmp_path / "package.whl").write_bytes(b"original")
    (tmp_path / "SHA256SUMS").write_text("\n".join(lines) + "\n")
    with pytest.raises(ReleaseError, match=match):
        verify_manifest(tmp_path, {"package.whl"})


def test_manifest_rejects_unlisted_file(tmp_path: Path) -> None:
    artifact = tmp_path / "package.whl"
    artifact.write_bytes(b"original")
    manifest([artifact], tmp_path)
    (tmp_path / "unexpected.txt").write_text("not a release asset")

    with pytest.raises(ReleaseError, match="unexpected files"):
        verify_manifest(tmp_path, {"package.whl"})


@pytest.mark.parametrize("archive_kind", ["zip", "tar"])
def test_archive_rejects_unsafe_member(tmp_path: Path, archive_kind: str) -> None:
    artifact = tmp_path / ("package.whl" if archive_kind == "zip" else "package.tgz")
    if archive_kind == "zip":
        with zipfile.ZipFile(artifact, "w") as archive:
            archive.writestr("../credential.txt", "secret")
    else:
        with tarfile.open(artifact, "w:gz") as archive:
            info = tarfile.TarInfo("../credential.txt")
            info.size = len(b"secret")
            archive.addfile(info, io.BytesIO(b"secret"))

    with pytest.raises(ReleaseError, match="unsafe path"):
        inspect_archive(artifact)


@pytest.mark.parametrize("name", ["package/.env", "package/node_modules/value.js"])
def test_tar_archive_rejects_forbidden_member(tmp_path: Path, name: str) -> None:
    artifact = tmp_path / "package.tgz"
    with tarfile.open(artifact, "w:gz") as archive:
        info = tarfile.TarInfo(name)
        info.size = len(b"value")
        archive.addfile(info, io.BytesIO(b"value"))

    with pytest.raises(ReleaseError, match="forbidden path"):
        inspect_archive(artifact)


@pytest.mark.parametrize(
    "content,match",
    [
        (b"OPENROUTER_API_KEY=real-secret-value", "populated secret"),
        (b"token = sk-abcdefghijklmnopqrstuvwxyz123456", "credential-shaped"),
        (f"path={Path.home()}/private".encode(), "developer-local path"),
    ],
)
def test_archive_rejects_secret_and_local_path_canaries(
    tmp_path: Path, content: bytes, match: str
) -> None:
    artifact = tmp_path / "package.whl"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("package/value.txt", content)

    with pytest.raises(ReleaseError, match=match):
        inspect_archive(artifact)


def test_archive_allows_documented_secret_placeholder(tmp_path: Path) -> None:
    artifact = tmp_path / "package.whl"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("package/.env", b"OPENROUTER_API_KEY=replace-with-real-key")

    with pytest.raises(ReleaseError, match="forbidden path"):
        inspect_archive(artifact)

    artifact = tmp_path / "safe.whl"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("package/example.txt", b"OPENROUTER_API_KEY=replace-with-real-key")
    inspect_archive(artifact)


def test_require_clean_rejects_dirty_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_release, "run", lambda *_args, **_kwargs: " M pyproject.toml\n")
    with pytest.raises(ReleaseError, match="clean Git working tree"):
        require_clean()


def test_release_version_rejects_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = tmp_path / "clients/typescript"
    source = tmp_path / "src/local_agent_runtime"
    contracts = tmp_path / "contracts"
    client.mkdir(parents=True)
    source.mkdir(parents=True)
    contracts.mkdir()
    (tmp_path / "pyproject.toml").write_text(f'[project]\nversion = "{VERSION}"\n')
    (client / "package.json").write_text(json.dumps({"version": "0.1.2"}))
    (client / "package-lock.json").write_text(json.dumps({"version": "0.1.0"}))
    (source / "version.py").write_text('PACKAGE_VERSION = "0.1.0"\n')
    (contracts / "openapi.json").write_text(json.dumps({"info": {"version": "1.0.0"}}))
    monkeypatch.setattr(build_release, "ROOT", tmp_path)
    monkeypatch.setattr(build_release, "CLIENT", client)

    with pytest.raises(ReleaseError, match="version mismatch"):
        release_version()


def test_release_version_rejects_api_contract_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = tmp_path / "clients/typescript"
    source = tmp_path / "src/local_agent_runtime"
    contracts = tmp_path / "contracts"
    client.mkdir(parents=True)
    source.mkdir(parents=True)
    contracts.mkdir()
    (tmp_path / "pyproject.toml").write_text(f'[project]\nversion = "{VERSION}"\n')
    (client / "package.json").write_text(json.dumps({"version": VERSION}))
    (client / "package-lock.json").write_text(json.dumps({"version": VERSION}))
    (source / "version.py").write_text(f'PACKAGE_VERSION = "{VERSION}"\n')
    (contracts / "openapi.json").write_text(json.dumps({"info": {"version": "9.9.9"}}))
    monkeypatch.setattr(build_release, "ROOT", tmp_path)
    monkeypatch.setattr(build_release, "CLIENT", client)

    with pytest.raises(ReleaseError, match="OpenAPI version"):
        release_version()


def test_sdist_membership_is_allowlisted(tmp_path: Path) -> None:
    artifact = tmp_path / f"local_agent_runtime-{VERSION}.tar.gz"
    root = f"local_agent_runtime-{VERSION}"
    names = [
        f"{root}/.gitignore",
        f"{root}/LICENSE",
        f"{root}/README.md",
        f"{root}/pyproject.toml",
        f"{root}/PKG-INFO",
        f"{root}/src/local_agent_runtime/__init__.py",
    ]
    with tarfile.open(artifact, "w:gz") as archive:
        for name in names:
            info = tarfile.TarInfo(name)
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
    inspect_sdist_members(artifact, VERSION)

    bad = tmp_path / "bad.tar.gz"
    with tarfile.open(bad, "w:gz") as archive:
        for name in [*names, f"{root}/tests/test_secret.py"]:
            info = tarfile.TarInfo(name)
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(ReleaseError, match="unexpected=.*tests/test_secret.py"):
        inspect_sdist_members(bad, VERSION)


def test_release_artifacts_must_carry_exact_repository_license(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    license_text = b"test license\n"
    (tmp_path / "LICENSE").write_bytes(license_text)
    monkeypatch.setattr(build_release, "ROOT", tmp_path)

    wheel = tmp_path / f"local_agent_runtime-{VERSION}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(f"local_agent_runtime-{VERSION}.dist-info/licenses/LICENSE", license_text)
    sdist = tmp_path / f"local_agent_runtime-{VERSION}.tar.gz"
    client = tmp_path / f"local-agent-runtime-client-{VERSION}.tgz"
    for path, member in [
        (sdist, f"local_agent_runtime-{VERSION}/LICENSE"),
        (client, "package/LICENSE"),
    ]:
        with tarfile.open(path, "w:gz") as archive:
            info = tarfile.TarInfo(member)
            info.size = len(license_text)
            archive.addfile(info, io.BytesIO(license_text))

    inspect_license_files([wheel, sdist, client], VERSION)
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(f"local_agent_runtime-{VERSION}.dist-info/licenses/LICENSE", b"wrong\n")
    with pytest.raises(ReleaseError, match="does not match"):
        inspect_license_files([wheel], VERSION)


def test_python_wheel_must_declare_its_inline_types(tmp_path: Path) -> None:
    wheel = tmp_path / f"local_agent_runtime-{VERSION}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("local_agent_runtime/__init__.py", "")
    with pytest.raises(ReleaseError, match="missing its PEP 561"):
        inspect_python_typing_marker(wheel)

    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("local_agent_runtime/py.typed", "partial\n")
    with pytest.raises(ReleaseError, match="must be empty"):
        inspect_python_typing_marker(wheel)

    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("local_agent_runtime/py.typed", "")
    inspect_python_typing_marker(wheel)


def test_source_commit_requires_full_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_release, "run", lambda *_args, **_kwargs: "not-a-commit\n")
    with pytest.raises(ReleaseError, match="source commit"):
        source_commit()


def test_dirty_build_cannot_create_release_manifest(tmp_path: Path) -> None:
    artifact = tmp_path / "package.whl"
    artifact.write_bytes(b"dirty development bytes")
    finalize_manifest(
        [artifact],
        tmp_path,
        version=VERSION,
        commit=COMMIT,
        release_eligible=False,
    )
    assert not (tmp_path / "SHA256SUMS").exists()
