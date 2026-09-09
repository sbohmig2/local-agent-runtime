"""Shared process lifecycle; subclasses own arguments, authentication and decoding."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from local_agent_runtime.adapters.cli_codec import _bounded_prompt, _output_schema, _parse_envelope
from local_agent_runtime.adapters.cli_environment import _safe_environment
from local_agent_runtime.adapters.process import (
    JsonRpcProcessRunner,
    ProcessResult,
    ProcessRunner,
    run_json_rpc_process,
    run_process,
)
from local_agent_runtime.configuration import validate_connection
from local_agent_runtime.contracts import (
    Capabilities,
    CompletionResult,
    HealthStatus,
    Invocation,
    ModelDiscovery,
    ModelProfile,
    ProviderConnection,
    ProviderHealth,
    ReasoningEffort,
)
from local_agent_runtime.errors import RuntimeFailure, provider_unavailable
from local_agent_runtime.reasoning import resolve_efforts


def _concrete_executable(path: str) -> str | None:
    """Resolve a launcher symlink to the installation binary it points at.

    A packaged CLI is normally reached through a symlink on PATH.  Some providers
    re-execute themselves during startup, and a restrictive sandbox refuses that
    re-execution when it is spelled as the symlink rather than the file it names.
    Resolving here keeps the executable identity honest without relaxing any
    filesystem boundary.  The trust anchor stays the validated configured command;
    the resolved file's own name is not re-checked, because packaged installations
    legitimately rename the target.
    """
    try:
        resolved = Path(path).resolve(strict=True)
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            return None
    except OSError:
        return None
    return str(resolved)


@dataclass
class CLIAdapterBase(ABC):
    connection: ProviderConnection
    profile: ModelProfile
    runner: ProcessRunner = run_process
    which: Callable[[str], str | None] = shutil.which
    resolve: Callable[[str], str | None] = _concrete_executable
    json_rpc_runner: JsonRpcProcessRunner = run_json_rpc_process

    #: Efforts the route can encode as provider arguments at all.
    TRANSPORT_EFFORTS: ClassVar[tuple[ReasoningEffort, ...]] = ()
    #: Efforts observed accepted by one exact model. An unlisted model inherits none.
    VERIFIED_EFFORTS: ClassVar[Mapping[str, tuple[ReasoningEffort, ...]]] = {}

    def __post_init__(self) -> None:
        validate_connection(self.connection)

    @property
    def capabilities(self) -> Capabilities:
        # CLI envelope tools are consumer-brokered, never native host tools.
        return Capabilities(reasoning_effort_control=bool(self.reasoning_efforts))

    @property
    def reasoning_efforts(self) -> tuple[ReasoningEffort, ...]:
        return resolve_efforts(self.profile, self.TRANSPORT_EFFORTS, self.VERIFIED_EFFORTS)

    async def discover_models(self) -> ModelDiscovery:
        """CLI routes expose no machine-readable catalog; identities stay configured."""
        return ModelDiscovery(supported=False, detail_code="discovery_unsupported")

    def executable(self) -> str | None:
        """Locate the trusted command, then the concrete binary it resolves to.

        Health and completion share this resolution so they describe the same
        installation.  Re-resolved on every invocation, so a CLI update is picked
        up and no release path is ever retained.
        """
        located = self.which(self.connection.command or "")
        return None if located is None else self.resolve(located)

    def requested_effort(self, invocation: Invocation) -> ReasoningEffort | None:
        effort = invocation.reasoning_effort
        if effort is None:
            return None
        if effort not in self.reasoning_efforts:
            raise RuntimeFailure(
                "reasoning_effort_unsupported",
                "The requested reasoning effort is not supported by this profile",
            )
        return effort

    def environment(self, root: Path) -> dict[str, str]:
        return _safe_environment()

    @abstractmethod
    def arguments(
        self, executable: str, root: Path, schema: Path, effort: ReasoningEffort | None
    ) -> list[str]: ...

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
        executable = self.executable()
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
        executable = self.executable()
        if executable is None:
            raise provider_unavailable()
        prompt = _bounded_prompt(invocation)
        effort = self.requested_effort(invocation)
        with tempfile.TemporaryDirectory(prefix="lar-provider-") as directory:
            root = Path(directory)
            schema = root / "output-schema.json"
            catalog = tuple(tool.name for tool in invocation.tools)
            schema.write_text(json.dumps(_output_schema(catalog)), encoding="utf-8")
            schema.chmod(0o600)
            arguments = self.arguments(executable, root, schema, effort)
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
        # Bound the raw process output before any native wrapper is parsed, so a
        # large `thought` or unknown field cannot slip past the profile's limit.
        if len(result.stdout) > invocation.limits.max_output_chars:
            raise RuntimeFailure("output_limit_exceeded", "The provider output exceeds its limit")
        # A forwarded effort is a request, not provider confirmation. Effective
        # effort stays unknown unless native metadata establishes it.
        return self.decode(result.stdout)
