"""Restricted CLI environment and private Grok credential copy."""

import os
import stat
from pathlib import Path

MAX_GROK_AUTH_BYTES = 64 * 1024


def _safe_environment() -> dict[str, str]:
    """Return only provider-neutral process settings.

    Provider credentials and provider-specific configuration roots are added by
    the owning adapter.  Keeping them out of this shared baseline prevents one
    vendor binary from receiving another vendor's authentication material.
    """
    allowed = {
        "PATH",
        "HOME",
        "TMPDIR",
        "XDG_CONFIG_HOME",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "NO_PROXY",
    }
    return {name: value for name, value in os.environ.items() if name in allowed}


def _provider_environment(*names: str) -> dict[str, str]:
    environment = _safe_environment()
    for name in names:
        value = os.environ.get(name)
        if value is not None:
            environment[name] = value
    return environment


def _copy_grok_auth(source_home: Path, target_home: Path) -> None:
    source = source_home / ".grok" / "auth.json"
    descriptor: int | None = None
    try:
        source_stat = source.lstat()
        if (
            not stat.S_ISREG(source_stat.st_mode)
            or source_stat.st_size > MAX_GROK_AUTH_BYTES
            or (os.name != "nt" and source_stat.st_mode & 0o077)
        ):
            return
        descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != source_stat.st_dev
            or opened.st_ino != source_stat.st_ino
            or opened.st_size > MAX_GROK_AUTH_BYTES
        ):
            return
        chunks = bytearray()
        while chunk := os.read(descriptor, 8_192):
            if len(chunks) + len(chunk) > MAX_GROK_AUTH_BYTES:
                return
            chunks.extend(chunk)
        final = os.fstat(descriptor)
        if (
            not chunks
            or len(chunks) != opened.st_size
            or final.st_dev != opened.st_dev
            or final.st_ino != opened.st_ino
            or final.st_size != opened.st_size
        ):
            return
        payload = bytes(chunks)
    except OSError:
        return
    finally:
        if descriptor is not None:
            os.close(descriptor)
    directory = target_home / ".grok"
    directory.mkdir(mode=0o700)
    target = directory / "auth.json"
    target.write_bytes(payload)
    if os.name != "nt":
        target.chmod(0o600)
