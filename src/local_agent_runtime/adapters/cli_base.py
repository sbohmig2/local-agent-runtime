"""Shared process lifecycle; subclasses own arguments, authentication and decoding."""

from __future__ import annotations

import json
import shutil
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from local_agent_runtime.adapters.cli_codec import _bounded_prompt, _output_schema, _parse_envelope
from local_agent_runtime.adapters.cli_environment import _safe_environment
from local_agent_runtime.adapters.process import ProcessResult, ProcessRunner, run_process
from local_agent_runtime.configuration import validate_connection
from local_agent_runtime.contracts import (
    Capabilities,
    CompletionResult,
    HealthStatus,
    Invocation,
    ModelProfile,
    ProviderConnection,
    ProviderHealth,
)
from local_agent_runtime.errors import RuntimeFailure, provider_unavailable


@dataclass
class CLIAdapterBase(ABC):
    connection: ProviderConnection
    profile: ModelProfile
    runner: ProcessRunner = run_process
    which: Callable[[str], str | None] = shutil.which

    def __post_init__(self) -> None:
        validate_connection(self.connection)

    @property
    def capabilities(self) -> Capabilities:
        # CLI envelope tools are consumer-brokered, never native host tools.
        return Capabilities()

    def environment(self, root: Path) -> dict[str, str]:
        return _safe_environment()

    @abstractmethod
    def arguments(self, executable: str, root: Path, schema: Path) -> list[str]: ...

    @abstractmethod
    def auth_arguments(self, executable: str) -> list[str] | None: ...

    def authenticated(self, result: ProcessResult) -> bool | None:
        return result.returncode == 0

    def decode(self, raw: str) -> CompletionResult:
        return _parse_envelope(raw, self.profile)

    def input(self, prompt: str, root: Path) -> str | None:
        return prompt

    async def cleanup(self, arguments: list[str], root: Path, environment: dict[str, str]) -> None:
        """Provider hook for cleanup before removing the private workspace."""
        return None

    async def health(self) -> ProviderHealth:
        executable = self.which(self.connection.command or "")
        if executable is None:
            return ProviderHealth(
                HealthStatus.UNAVAILABLE,
                installed=False,
                authenticated=None,
                compatible=None,
                detail_code="executable_unavailable",
            )
        try:
            with tempfile.TemporaryDirectory(prefix="lar-health-") as directory:
                root = Path(directory)
                environment = self.environment(root)
                version = await self.runner([executable, "--version"], None, root, environment, 15)
                if version.returncode != 0:
                    return ProviderHealth(
                        HealthStatus.UNAVAILABLE,
                        installed=True,
                        authenticated=None,
                        compatible=False,
                        detail_code="version_probe_failed",
                    )
                args = self.auth_arguments(executable)
                if args is None:
                    return ProviderHealth(
                        HealthStatus.INCONCLUSIVE,
                        installed=True,
                        authenticated=None,
                        compatible=None,
                        detail_code="authentication_not_probed",
                    )
                authenticated = self.authenticated(
                    await self.runner(args, None, root, environment, 15)
                )
                return ProviderHealth(
                    HealthStatus.INCONCLUSIVE
                    if authenticated is None
                    else HealthStatus.AVAILABLE
                    if authenticated
                    else HealthStatus.UNAVAILABLE,
                    installed=True,
                    authenticated=authenticated,
                    compatible=None,
                    detail_code=(
                        "invocation_not_qualified"
                        if authenticated is True
                        else "authentication_unavailable"
                        if authenticated is False
                        else "authentication_inconclusive"
                    ),
                )
        except RuntimeFailure:
            return ProviderHealth(
                HealthStatus.UNAVAILABLE,
                installed=None,
                authenticated=None,
                compatible=None,
                detail_code="health_probe_failed",
            )

    async def complete(self, invocation: Invocation) -> CompletionResult:
        executable = self.which(self.connection.command or "")
        if executable is None:
            raise provider_unavailable()
        prompt = _bounded_prompt(invocation)
        with tempfile.TemporaryDirectory(prefix="lar-provider-") as directory:
            root = Path(directory)
            schema = root / "output-schema.json"
            schema.write_text(json.dumps(_output_schema()), encoding="utf-8")
            schema.chmod(0o600)
            arguments = self.arguments(executable, root, schema)
            environment = self.environment(root)
            try:
                result = await self.runner(
                    arguments,
                    self.input(prompt, root),
                    root,
                    environment,
                    invocation.limits.timeout_seconds,
                )
            finally:
                await self.cleanup(arguments, root, environment)
        if result.returncode != 0:
            raise provider_unavailable("The provider process failed without exposing output")
        return self.decode(result.stdout)
