"""Opt-in synthetic evidence against really installed providers.

These tests are skipped unless ``LAR_LIVE_PROVIDERS`` names the profiles to
exercise, because they start real provider processes or reach a real endpoint.
They prove one thing the fake suite cannot: that a configured profile completes a
tool-assisted turn and that a supported reasoning effort survives the round trip.

    LAR_LIVE_PROVIDERS=lm-studio-local,codex-default uv run pytest tests/test_live_providers.py
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from local_agent_runtime.adapters.selection import SelectionStore
from local_agent_runtime.contracts import SessionStatus, ToolDefinition, ToolResult
from local_agent_runtime.providers import build_provider
from local_agent_runtime.service import RuntimeService

CONFIGURATION = (
    Path(os.environ.get("LAR_LIVE_CONFIG", "")) if os.environ.get("LAR_LIVE_CONFIG") else None
)
SELECTED = [item for item in os.environ.get("LAR_LIVE_PROVIDERS", "").split(",") if item]
TOOL = ToolDefinition(
    "vault_balance",
    "Return the balance of one synthetic account. Call it before answering.",
    {
        "type": "object",
        "properties": {"account": {"type": "string"}},
        "required": ["account"],
        "additionalProperties": False,
    },
)
PROMPT = (
    "Call vault_balance for account 'demo', then answer with the returned balance "
    "as a bare number and nothing else."
)


def service() -> RuntimeService:
    from local_agent_runtime.configuration import load_configuration

    if CONFIGURATION is None:
        pytest.skip("LAR_LIVE_CONFIG must point at a real runtime configuration")
    return RuntimeService(
        load_configuration(CONFIGURATION),
        SelectionStore(Path(os.environ.get("TMPDIR", "/tmp")) / "lar-live-state"),
        provider_factory=build_provider,
    )


@pytest.mark.skipif(not SELECTED, reason="LAR_LIVE_PROVIDERS is not set")
@pytest.mark.parametrize("profile_id", SELECTED)
def test_configured_profile_completes_a_synthetic_tool_assisted_turn(profile_id: str) -> None:
    async def run() -> None:
        runtime = service()
        profile = runtime.configuration.profiles[profile_id]
        connection = runtime.configuration.providers[profile.provider_id]
        efforts = runtime.provider_factory(connection, profile).reasoning_efforts
        effort = efforts[-1].value if efforts else None

        created = await runtime.create_session(
            PROMPT,
            (TOOL,),
            profile_id=profile_id,
            allow_external_processing=profile.allow_external_processing,
            reasoning_effort=effort,
        )
        state = await runtime.wait(created["id"])
        assert state["status"] == SessionStatus.WAITING_FOR_TOOL.value, state["failure"]
        pending = state["pending_tools"]
        assert [item["name"] for item in pending] == ["vault_balance"]

        await runtime.submit_tool_results(
            created["id"],
            [ToolResult(pending[0]["id"], "vault_balance", {"balance": 4321}, False)],
        )
        settled = await runtime.wait(created["id"])
        assert settled["status"] == SessionStatus.COMPLETED.value, settled["failure"]
        assert "4321" in (settled["final_text"] or "")
        assert settled["requested_reasoning_effort"] == effort
        # No installed route reports the level it applied, so it must stay unknown.
        assert settled["effective_reasoning_effort"] is None
        if connection.driver == "lmstudio":
            events = runtime.events(created["id"])
            deltas = [event for event in events if event["type"] == "assistant_text_delta"]
            assert deltas
            assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
            final_round = max(event["payload"]["round"] for event in deltas)
            assert (
                "".join(
                    event["payload"]["delta"]
                    for event in deltas
                    if event["payload"]["round"] == final_round
                )
                == settled["final_text"]
            )

    asyncio.run(run())
