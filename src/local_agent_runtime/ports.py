"""Application ports implemented by provider adapters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol

from local_agent_runtime.contracts import (
    Capabilities,
    CompletionResult,
    Invocation,
    ModelDiscovery,
    ModelProfile,
    ProviderConnection,
    ProviderHealth,
    ReasoningEffort,
)


class ProviderPort(Protocol):
    connection: ProviderConnection
    profile: ModelProfile

    @property
    def capabilities(self) -> Capabilities: ...

    @property
    def reasoning_efforts(self) -> tuple[ReasoningEffort, ...]:
        """Efforts this adapter actually delivers to its provider; empty means none."""
        ...

    async def health(self) -> ProviderHealth: ...

    async def discover_models(self) -> ModelDiscovery: ...

    async def complete(self, invocation: Invocation) -> CompletionResult: ...


TextDeltaSink = Callable[[str], Awaitable[None]]


class StreamingProviderPort(ProviderPort, Protocol):
    """Optional additive port for providers with display-safe native text deltas."""

    async def complete_streaming(
        self, invocation: Invocation, emit_text: TextDeltaSink
    ) -> CompletionResult: ...


ProviderFactory = Callable[[ProviderConnection, ModelProfile], ProviderPort]


class SelectionPort(Protocol):
    def read(self, default: str) -> str: ...

    def write(self, profile_id: str) -> None: ...


class ActivationPort(Protocol):
    def read(self) -> Mapping[str, bool]: ...

    def write(self, state: Mapping[str, bool]) -> None: ...


class AdapterCatalogPort(Protocol):
    def supported(self) -> Sequence[Mapping[str, Any]]: ...

    async def probe(
        self, driver: str, connections: Sequence[ProviderConnection]
    ) -> Mapping[str, Any]: ...
