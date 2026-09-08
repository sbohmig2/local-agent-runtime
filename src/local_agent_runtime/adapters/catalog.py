"""Allowlisted adapter inventory and bounded content-free installation detection."""

import asyncio
import shutil
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from local_agent_runtime.adapters.cli_base import _concrete_executable
from local_agent_runtime.adapters.providers.lmstudio import LMStudioAdapter
from local_agent_runtime.configuration import CLI_COMMANDS, DEFAULT_ENDPOINTS
from local_agent_runtime.contracts import ModelProfile, ProcessingClass, ProviderConnection


class AdapterCatalog:
    def __init__(self, which: Callable[[str], str | None] = shutil.which) -> None:
        self.which = which

    def supported(self) -> Sequence[Mapping[str, Any]]:
        return [
            {"id": driver, "label": label, "processing": processing}
            for driver, label, processing in (
                ("codex_cli", "Codex", "external"),
                ("claude_cli", "Claude Code", "external"),
                ("grok_cli", "Grok", "external"),
                ("lmstudio", "LM Studio", "local"),
                ("openrouter", "OpenRouter", "external"),
            )
        ]

    async def probe(
        self, driver: str, connections: Sequence[ProviderConnection]
    ) -> Mapping[str, Any]:
        if driver in CLI_COMMANDS:
            # Detect only trusted configured commands, or the known PATH command
            # when no connection exists. No process or authentication flow is run.
            commands = [c.command for c in connections] or [CLI_COMMANDS[driver]]

            def detect() -> bool:
                return any(
                    located is not None and _concrete_executable(located) is not None
                    for command in commands
                    for located in [self.which(command or "")]
                )

            installed = await asyncio.to_thread(detect)
            return {
                "installed": installed,
                "detail_code": None if installed else "executable_unavailable",
            }
        if driver == "lmstudio":
            # An adapter-wide discovery would be ambiguous with multiple endpoints.
            if len(connections) > 1:
                return {"installed": None, "detail_code": "multiple_connections_check_profiles"}
            connection = (
                connections[0]
                if connections
                else ProviderConnection(
                    "catalog-lmstudio",
                    "lmstudio",
                    ProcessingClass.LOCAL,
                    endpoint=DEFAULT_ENDPOINTS["lmstudio"],
                )
            )
            adapter = LMStudioAdapter(
                connection, ModelProfile("catalog-probe", connection.id, "default", False, False)
            )
            discovery = await adapter.discover_models()
            return {
                # Endpoint availability is evidence of a serving installation;
                # an unreachable endpoint does not prove the app is uninstalled.
                "installed": True if discovery.detail_code is None else None,
                "detail_code": discovery.detail_code,
                "discovery": discovery.public_dict(),
            }
        return {
            "installed": None,
            "detail_code": "cloud_route" if connections else "setup_required",
        }
