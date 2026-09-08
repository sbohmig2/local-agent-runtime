"""Private atomic activation overlay for operator-approved profile options."""

import json
import os
import stat
import uuid
from collections.abc import Mapping
from pathlib import Path

from local_agent_runtime.errors import RuntimeFailure


class ActivationStore:
    def __init__(self, root: Path, policy_fingerprint: str) -> None:
        self.root = root.expanduser().absolute()
        self.path = self.root / "activation.json"
        self.policy_fingerprint = policy_fingerprint

    def _check_root(self) -> None:
        info = self.root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
            raise OSError

    @staticmethod
    def _failure() -> RuntimeFailure:
        return RuntimeFailure(
            "activation_unavailable",
            "The runtime activation setting is unavailable",
            status_code=503,
        )

    def read(self) -> Mapping[str, bool]:
        try:
            if self.root.exists():
                self._check_root()
            fd = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                    raise OSError
                content = handle.read(65_537)
            if len(content) > 65_536:
                raise ValueError
            payload = json.loads(content)
            if (
                not isinstance(payload, dict)
                or set(payload) != {"schema_version", "policy_fingerprint", "profiles"}
                or type(payload["schema_version"]) is not int
                or payload["schema_version"] != 1
                or payload["policy_fingerprint"] != self.policy_fingerprint
                or not isinstance(payload["profiles"], dict)
                or any(type(v) is not bool for v in payload["profiles"].values())
            ):
                raise ValueError
            return dict(payload["profiles"])
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, UnicodeError):
            raise self._failure() from None

    def write(self, state: Mapping[str, bool]) -> None:
        temporary = self.root / f".activation-{uuid.uuid4().hex}.tmp"
        try:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._check_root()
            if self.path.is_symlink():
                raise OSError
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "schema_version": 1,
                        "policy_fingerprint": self.policy_fingerprint,
                        "profiles": dict(state),
                    },
                    handle,
                    separators=(",", ":"),
                )
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self.path)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise self._failure() from None
