from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path
from typing import cast

import httpx
import pytest
from jsonschema import Draft202012Validator

from local_agent_runtime.adapters.selection import SelectionStore
from local_agent_runtime.api_contract import SCHEMAS
from local_agent_runtime.configuration import load_configuration
from local_agent_runtime.contracts import (
    Capabilities,
    CompletionResult,
    HealthStatus,
    Invocation,
    Limits,
    Message,
    ModelProfile,
    ProcessingClass,
    ProviderConnection,
    ProviderHealth,
    RuntimeConfiguration,
    ToolDefinition,
    ToolRequest,
    ToolResult,
)
from local_agent_runtime.embeddings import EmbeddingProfile, EmbeddingResult, EmbeddingService
from local_agent_runtime.errors import RuntimeFailure
from local_agent_runtime.gateway import create_app
from local_agent_runtime.providers import build_provider
from local_agent_runtime.service import RuntimeService

TOKEN = "t" * 40
TOOL = ToolDefinition(
    "lookup",
    "Get a value",
    {
        "type": "object",
        "properties": {"id": {"type": "integer"}},
        "required": ["id"],
        "additionalProperties": False,
    },
)


class FakeProvider:
    capabilities = Capabilities()

    def __init__(self, connection: ProviderConnection, profile: ModelProfile) -> None:
        self.connection = connection
        self.profile = profile
        self.result = CompletionResult("answer", (), "model")
        self.invocations: list[Invocation] = []
        self.delay_seconds = 0.0
        self.health_calls = 0

    async def health(self) -> ProviderHealth:
        self.health_calls += 1
        return ProviderHealth(
            HealthStatus.AVAILABLE,
            installed=True,
            authenticated=None,
            compatible=True,
        )

    async def complete(self, invocation: Invocation) -> CompletionResult:
        self.invocations.append(invocation)
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        return self.result


def runtime(tmp_path: Path) -> tuple[RuntimeService, FakeProvider]:
    connection = ProviderConnection(
        "local", "lmstudio", ProcessingClass.LOCAL, endpoint="http://127.0.0.1:1234/v1"
    )
    profile = ModelProfile("reason", "local", "model", False, True)
    config = RuntimeConfiguration(
        {"local": connection}, {"reason": profile}, "reason", {"answer": "reason"}
    )
    fake = FakeProvider(connection, profile)
    return RuntimeService(
        config, SelectionStore(tmp_path / "state"), provider_factory=lambda *_: fake
    ), fake


def assert_schema(name: str, data: object) -> None:
    schema = {**SCHEMAS[name], "components": {"schemas": SCHEMAS}}
    Draft202012Validator(schema).validate(data)


def test_shared_conformance_fixture() -> None:
    fixture = json.loads(
        (Path(__file__).resolve().parents[1] / "contracts/conformance.json").read_text()
    )
    assert_schema("ErrorResponse", fixture["error"])
    assert_schema("EventsResponse", {"events": fixture["events"]})
    assert [event["sequence"] for event in fixture["events"]] == [1, 2]


def test_session_tool_loop_and_atomic_results(tmp_path: Path) -> None:
    async def run() -> None:
        app, fake = runtime(tmp_path)
        fake.result = CompletionResult(
            "",
            (ToolRequest("one", "lookup", {"id": 1}), ToolRequest("two", "lookup", {"id": 2})),
            "model",
        )
        session = await app.create_session(
            "hello", [TOOL], task_code="answer", instructions="Use only supplied tools."
        )
        session = await app.wait(session["id"])
        assert session["status"] == "waiting_for_tool"
        record = app.sessions[session["id"]]
        before = list(record.messages)
        with pytest.raises(RuntimeFailure):
            await app.submit_tool_results(
                session["id"], [ToolResult("one", "lookup", 1), ToolResult("two", "wrong", 2)]
            )
        assert record.messages == before
        fake.result = CompletionResult("done", (), "model")
        await app.submit_tool_results(
            session["id"], [ToolResult("one", "lookup", 1), ToolResult("two", "lookup", 2)]
        )
        final = await app.wait(session["id"])
        assert final["status"] == "completed"
        assert_schema("SessionResponse", final)
        events = app.events(session["id"])
        assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
        assert_schema("EventsResponse", {"events": events})
        assert fake.invocations[-1].messages[-1].tool_request_id == "two"
        assert fake.invocations[0].messages[:2] == (
            Message("system", "Use only supplied tools."),
            Message("user", "hello"),
        )

    asyncio.run(run())


@pytest.mark.parametrize(
    "tool_request,code",
    [
        (ToolRequest("1", "unknown", {}), "unknown_tool_request"),
        (ToolRequest("1", "lookup", {"id": "bad"}), "invalid_tool_arguments"),
    ],
)
def test_invalid_model_tools_fail(tmp_path: Path, tool_request: ToolRequest, code: str) -> None:
    async def run() -> None:
        app, fake = runtime(tmp_path)
        fake.result = CompletionResult("", (tool_request,), "model")
        created = await app.create_session("hello", [TOOL])
        final = await app.wait(created["id"])
        assert final["status"] == "failed"
        assert final["failure"]["code"] == code

    asyncio.run(run())


def test_unknown_task_and_remote_schema_are_rejected(tmp_path: Path) -> None:
    async def run() -> None:
        app, fake = runtime(tmp_path)
        with pytest.raises(RuntimeFailure):
            await app.create_session("hello", task_code="typo")
        with pytest.raises(RuntimeFailure):
            await app.create_session(
                "hello", [ToolDefinition("bad", "", {"$ref": "https://example.com/schema"})]
            )
        assert fake.invocations == []

    asyncio.run(run())


def test_structured_output_and_immediate_cancel(tmp_path: Path) -> None:
    async def run() -> None:
        app, fake = runtime(tmp_path)
        created = await app.create_session("hello", output_schema={"type": "object"})
        final = await app.wait(created["id"])
        assert final["failure"]["code"] == "schema_validation_failed"
        created = await app.create_session("hello")
        canceled = await app.cancel(created["id"])
        assert canceled["status"] == "canceled"
        assert len(fake.invocations) == 1

    asyncio.run(run())


def test_gateway_embeddings_auth_ipv6_and_schemas(tmp_path: Path) -> None:
    async def run() -> None:
        app, _ = runtime(tmp_path)
        profile = EmbeddingProfile("vectors", "local", "embed", 2)

        class Fake:
            async def embed(self, inputs: tuple[str, ...]) -> EmbeddingResult:
                return EmbeddingResult(((1.0, 2.0),), "embed")

        app.embeddings = EmbeddingService(
            app.configuration.providers, {"vectors": profile}, provider_factory=lambda *_: Fake()
        )
        gateway = create_app(app, bearer_token=TOKEN)
        remote_transport = httpx.ASGITransport(app=gateway, client=("203.0.113.10", 49152))
        async with httpx.AsyncClient(
            transport=remote_transport, base_url="http://127.0.0.1:8765"
        ) as remote:
            refused = await remote.get("/v1/health", headers={"Authorization": "Bearer " + TOKEN})
            assert refused.status_code == 403
            assert refused.json()["error"]["code"] == "non_loopback_refused"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gateway), base_url="http://[::1]:8765"
        ) as client:
            assert (await client.get("/v1/embedding-profiles")).status_code == 401
            headers = {"Authorization": "Bearer " + TOKEN}
            assert (
                await client.get(
                    "/v1/profiles", headers={**headers, "Origin": "https://evil.example"}
                )
            ).status_code == 403
            invalid_health = await client.get("/v1/profiles?health=yes", headers=headers)
            assert invalid_health.status_code == 400
            profiles = await client.get("/v1/embedding-profiles", headers=headers)
            assert profiles.status_code == 200
            assert_schema("EmbeddingProfilesResponse", profiles.json())
            result = await client.post(
                "/v1/embeddings",
                headers=headers,
                json={"profile_id": "vectors", "purpose": "query", "inputs": ["hello"]},
            )
            assert result.status_code == 200
            assert_schema("EmbeddingResponse", result.json())
            assert result.headers["cache-control"] == "no-store"
            health = await client.get("/v1/health", headers=headers)
            assert_schema("HealthResponse", health.json())
            assert health.json() == {
                "status": "available",
                "package_version": "0.1.3",
                "api_version": "1.0.0",
            }
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={"prompt": "hello", "instructions": "Stay concise."},
            )
            assert created.status_code == 200
            await app.wait(created.json()["id"])
            events = await client.get(
                f"/v1/sessions/{created.json()['id']}/events", headers=headers
            )
            assert events.status_code == 200
            assert events.headers["cache-control"] == "no-store"
            bad = await client.post(
                "/v1/embeddings",
                headers=headers,
                json={"profile_id": "vectors", "inputs": ["hello"], "purpose": "unknown"},
            )
            assert bad.status_code == 400
            assert_schema("ErrorResponse", bad.json())
            missing = await client.get(
                "/v1/sessions/missing/events", headers={**headers, "Accept": "text/event-stream"}
            )
            assert missing.status_code == 404
            oversized = await client.post(
                "/v1/embeddings", headers=headers, content=b"x" * 1_200_001
            )
            assert oversized.status_code == 400
        await app.shutdown()

    asyncio.run(run())


def test_session_rejects_invalid_instructions(tmp_path: Path) -> None:
    async def run() -> None:
        app, fake = runtime(tmp_path)
        for value in ("", 1, False):
            with pytest.raises(RuntimeFailure) as caught:
                await app.create_session("hello", instructions=value)
            assert caught.value.code == "invalid_request"
        assert fake.invocations == []

    asyncio.run(run())


def test_all_reasoning_drivers_share_application_boundary_and_fresh_sessions(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        drivers = ("codex_cli", "claude_cli", "grok_cli", "lmstudio", "openrouter")
        connections: dict[str, ProviderConnection] = {}
        profiles: dict[str, ModelProfile] = {}
        adapters: dict[str, FakeProvider] = {}
        for driver in drivers:
            identifier = driver.replace("_cli", "")
            if driver.endswith("_cli"):
                connection = ProviderConnection(
                    identifier, driver, ProcessingClass.EXTERNAL, command=identifier
                )
            elif driver == "lmstudio":
                connection = ProviderConnection(
                    identifier,
                    driver,
                    ProcessingClass.LOCAL,
                    endpoint="http://127.0.0.1:1234/v1",
                )
            else:
                connection = ProviderConnection(
                    identifier,
                    driver,
                    ProcessingClass.EXTERNAL,
                    endpoint="https://openrouter.ai/api/v1",
                    credential_ref="env://LAR_TEST_KEY",
                    upstream="openai",
                )
            profile = ModelProfile(
                identifier + "-profile",
                identifier,
                identifier + "-model",
                driver != "lmstudio",
                driver == "lmstudio",
                qualified_tasks=("analysis",) if driver == "codex_cli" else (),
            )
            connections[identifier] = connection
            profiles[profile.id] = profile
            adapters[profile.id] = FakeProvider(connection, profile)
            adapters[profile.id].result = CompletionResult("done", (), profile.model)
        config = RuntimeConfiguration(connections, profiles, "codex-profile")
        service = RuntimeService(
            config,
            SelectionStore(tmp_path / "state"),
            provider_factory=lambda _connection, profile: adapters[profile.id],
        )

        for index, profile in enumerate(profiles.values()):
            await service.select_profile(profile.id)
            created = await service.create_session(
                f"prompt-{index}",
                allow_external_processing=profile.allow_external_processing,
            )
            final = await service.wait(created["id"])
            assert final["status"] == "completed"
            invocation = adapters[profile.id].invocations[0]
            assert [message.content for message in invocation.messages] == [f"prompt-{index}"]

        state = await service.profile_state(include_health=True)
        assert_schema("ProfilesResponse", state)
        assert {item["driver"] for item in state["profiles"]} == set(drivers)
        for item in state["profiles"]:
            assert item["configured"] is True
            assert item["health"]["installed"] is True
            assert item["health"]["compatible"] is True
            expected = "qualified" if item["driver"] == "codex_cli" else "unqualified"
            assert item["qualification"]["status"] == expected

    asyncio.run(run())


def test_real_provider_registry_composes_every_profile_through_service(tmp_path: Path) -> None:
    configuration = load_configuration(
        Path(__file__).resolve().parents[1] / "config/runtime.example.yaml"
    )
    service = RuntimeService(
        configuration,
        SelectionStore(tmp_path / "state"),
        provider_factory=build_provider,
    )
    state = asyncio.run(service.profile_state())
    assert {item["driver"] for item in state["profiles"]} == {
        "codex_cli",
        "claude_cli",
        "grok_cli",
        "lmstudio",
        "openrouter",
    }
    assert all(item["capabilities"]["text_generation"] is True for item in state["profiles"])


def test_external_session_requires_per_request_disclosure(tmp_path: Path) -> None:
    connection = ProviderConnection(
        "remote", "codex_cli", ProcessingClass.EXTERNAL, command="codex"
    )
    profile = ModelProfile("remote", "remote", "model", True, False)
    configuration = RuntimeConfiguration({"remote": connection}, {"remote": profile}, "remote")
    fake = FakeProvider(connection, profile)
    service = RuntimeService(
        configuration,
        SelectionStore(tmp_path / "state"),
        provider_factory=lambda *_: fake,
    )
    with pytest.raises(RuntimeFailure) as caught:
        asyncio.run(service.create_session("content-canary"))
    assert caught.value.code == "processing_not_allowed"
    assert "canary" not in str(caught.value)
    assert fake.invocations == []


def test_service_timeout_round_exhaustion_and_late_tool_result(tmp_path: Path) -> None:
    async def run() -> None:
        app, fake = runtime(tmp_path)

        profiles = cast(dict[str, ModelProfile], app.configuration.profiles)
        profiles["reason"] = ModelProfile(
            "reason", "local", "model", False, True, Limits(timeout_seconds=1)
        )
        fake.profile = app.configuration.profiles["reason"]
        fake.delay_seconds = 30
        created = await app.create_session("timeout")
        final = await app.wait(created["id"])
        assert final["failure"]["code"] == "provider_timeout"

        app, fake = runtime(tmp_path / "round")
        profiles = cast(dict[str, ModelProfile], app.configuration.profiles)
        profiles["reason"] = ModelProfile(
            "reason", "local", "model", False, True, Limits(max_tool_rounds=0)
        )
        fake.profile = app.configuration.profiles["reason"]
        fake.result = CompletionResult("", (ToolRequest("one", "lookup", {"id": 1}),), "model")
        created = await app.create_session("round", [TOOL])
        final = await app.wait(created["id"])
        assert final["failure"]["code"] == "tool_round_limit_exceeded"
        with pytest.raises(RuntimeFailure):
            await app.submit_tool_results(
                created["id"], [ToolResult("one", "lookup", {"value": 1})]
            )

    asyncio.run(run())


def test_gateway_restart_keeps_selection_but_not_session_content(tmp_path: Path) -> None:
    async def run() -> None:
        first, _ = runtime(tmp_path)
        created = await first.create_session("content-canary")
        await first.wait(created["id"])
        second, _ = runtime(tmp_path)
        assert second.selected_profile() == "reason"
        with pytest.raises(RuntimeFailure) as caught:
            second.session(created["id"])
        assert caught.value.code == "not_found"
        assert "canary" not in str(caught.value)

    asyncio.run(run())


def test_profile_inspection_is_passive_until_health_is_requested(tmp_path: Path) -> None:
    async def run() -> None:
        app, fake = runtime(tmp_path)
        state = await app.profile_state()
        assert "health" not in state["profiles"][0]
        assert fake.health_calls == 0
        state = await app.profile_state(include_health=True)
        assert state["profiles"][0]["health"]["compatible"] is True
        assert fake.health_calls == 1

    asyncio.run(run())


def test_switch_during_run_pins_old_session_and_new_session_starts_fresh(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        first_connection = ProviderConnection(
            "first",
            "lmstudio",
            ProcessingClass.LOCAL,
            endpoint="http://127.0.0.1:1234/v1",
        )
        second_connection = ProviderConnection(
            "second",
            "lmstudio",
            ProcessingClass.LOCAL,
            endpoint="http://127.0.0.1:1235/v1",
        )
        first_profile = ModelProfile("first-profile", "first", "model-one", False, True)
        second_profile = ModelProfile("second-profile", "second", "model-two", False, True)
        entered = asyncio.Event()
        release = asyncio.Event()

        class ControlledProvider(FakeProvider):
            async def complete(self, invocation: Invocation) -> CompletionResult:
                self.invocations.append(invocation)
                if self.profile.id == "first-profile":
                    entered.set()
                    await release.wait()
                return CompletionResult("done", (), self.profile.model)

        adapters = {
            first_profile.id: ControlledProvider(first_connection, first_profile),
            second_profile.id: ControlledProvider(second_connection, second_profile),
        }
        app = RuntimeService(
            RuntimeConfiguration(
                {"first": first_connection, "second": second_connection},
                {"first-profile": first_profile, "second-profile": second_profile},
                "first-profile",
            ),
            SelectionStore(tmp_path / "state"),
            provider_factory=lambda _connection, profile: adapters[profile.id],
        )
        old = await app.create_session("old context")
        await entered.wait()
        await app.select_profile("second-profile")
        new = await app.create_session("new context")
        release.set()
        old_final, new_final = await asyncio.gather(app.wait(old["id"]), app.wait(new["id"]))
        assert old_final["profile_id"] == "first-profile"
        assert old_final["effective_model"] == "model-one"
        assert new_final["profile_id"] == "second-profile"
        assert [m.content for m in adapters["second-profile"].invocations[0].messages] == [
            "new context"
        ]

    asyncio.run(run())


def test_stale_sessions_expire_without_exposing_content(tmp_path: Path) -> None:
    async def run() -> None:
        app, _ = runtime(tmp_path)
        created = await app.create_session("stale-content-canary")
        await app.wait(created["id"])
        app.sessions[created["id"]].updated_at -= timedelta(seconds=1801)
        with pytest.raises(RuntimeFailure) as caught:
            app.session(created["id"])
        assert caught.value.code == "not_found"
        assert "canary" not in str(caught.value)
        assert created["id"] not in app.sessions

    asyncio.run(run())


def test_concurrency_limit_refuses_ninth_running_session(tmp_path: Path) -> None:
    async def run() -> None:
        app, fake = runtime(tmp_path)
        fake.delay_seconds = 30
        sessions = [await app.create_session(f"request-{index}") for index in range(8)]
        with pytest.raises(RuntimeFailure) as caught:
            await app.create_session("request-nine")
        assert caught.value.code == "capacity_exceeded"
        await asyncio.gather(*(app.cancel(item["id"]) for item in sessions))
        assert all(app.session(item["id"])["status"] == "canceled" for item in sessions)

    asyncio.run(run())


def test_gateway_cancel_and_future_event_cursor(tmp_path: Path) -> None:
    async def run() -> None:
        app, fake = runtime(tmp_path)
        fake.delay_seconds = 30
        gateway = create_app(app, bearer_token=TOKEN)
        headers = {"Authorization": "Bearer " + TOKEN}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gateway),
            base_url="http://127.0.0.1:8765",
        ) as client:
            created = await client.post(
                "/v1/sessions", headers=headers, json={"prompt": "cancel me"}
            )
            assert created.status_code == 200
            session_id = created.json()["id"]
            future = await client.get(
                f"/v1/sessions/{session_id}/events?after=999", headers=headers
            )
            assert future.status_code == 400
            canceled = await client.post(f"/v1/sessions/{session_id}/cancel", headers=headers)
            assert canceled.status_code == 200
            assert canceled.json()["status"] == "canceled"
            assert canceled.json()["validation"] == "not_validated"

    asyncio.run(run())
