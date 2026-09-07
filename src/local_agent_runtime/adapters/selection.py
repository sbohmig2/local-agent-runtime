"""Private deployment-local selection persistence."""

import json
import os
import stat
import uuid
from collections.abc import Mapping
from pathlib import Path

from local_agent_runtime.errors import RuntimeFailure


class SelectionStore:
    """Persist only a selected profile identifier as protected local state."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().absolute()
        self.path = self.root / "selection.json"

    def read(self, default: str) -> str:
        try:
            if self.root.exists():
                self._check_root()
            descriptor = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "rb") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                    raise OSError
                content = handle.read(4097)
            if len(content) > 4096:
                raise OSError
            payload = json.loads(content)
        except FileNotFoundError:
            return default
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise RuntimeFailure(
                "selection_unavailable",
                "The selected profile setting is unavailable",
                status_code=503,
            ) from None
        if (
            not isinstance(payload, Mapping)
            or set(payload) != {"schema_version", "profile_id"}
            or payload["schema_version"] != 1
            or not isinstance(payload["profile_id"], str)
        ):
            raise RuntimeFailure(
                "selection_unavailable",
                "The selected profile setting is invalid",
                status_code=503,
            )
        return payload["profile_id"]

    def write(self, profile_id: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._check_root()
        if self.root.is_symlink() or self.path.is_symlink():
            raise RuntimeFailure(
                "selection_unavailable",
                "The selected profile setting is unavailable",
                status_code=503,
            )
        temporary = self.root / f".selection-{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                if os.name != "nt":
                    os.chmod(handle.fileno(), 0o600)
                json.dump(
                    {"schema_version": 1, "profile_id": profile_id},
                    handle,
                    separators=(",", ":"),
                )
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self.path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise RuntimeFailure(
                "selection_unavailable",
                "The selected profile setting could not be saved",
                status_code=503,
            ) from exc

    def _check_root(self) -> None:
        info = self.root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
            raise RuntimeFailure(
                "selection_unavailable", "Selection directory must be private", status_code=503
            )
