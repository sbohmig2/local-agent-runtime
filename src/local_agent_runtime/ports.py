"""Application ports implemented by provider adapters."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from local_agent_runtime.contracts import (
    Capabilities,
    CompletionResult,
    Invocation,
    ModelProfile,
    ProviderConnection,
    ProviderHealth,
)


class ProviderPort(Protocol):
    connection: ProviderConnection
    profile: ModelProfile

    @property
    def capabilities(self) -> Capabilities: ...

    async def health(self) -> ProviderHealth: ...

    async def complete(self, invocation: Invocation) -> CompletionResult: ...


ProviderFactory = Callable[[ProviderConnection, ModelProfile], ProviderPort]


class SelectionPort(Protocol):
    def read(self, default: str) -> str: ...

    def write(self, profile_id: str) -> None: ...
