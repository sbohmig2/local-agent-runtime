from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from jsonschema import Draft202012Validator

import local_agent_runtime.gateway as gateway_module
import local_agent_runtime.service as service_module
from local_agent_runtime.adapters.chat_codec import chat_body, chat_prompt_chars
from local_agent_runtime.adapters.cli_codec import _bounded_prompt as cli_bounded_prompt
from local_agent_runtime.adapters.cli_codec import cli_prompt_chars
from local_agent_runtime.adapters.providers.lmstudio import LMStudioAdapter
from local_agent_runtime.adapters.selection import SelectionStore
from local_agent_runtime.api_contract import API_VERSION, SCHEMAS
from local_agent_runtime.configuration import load_configuration
from local_agent_runtime.context import (
    INPUT_IRREDUCIBLE_MESSAGE,
    IRREDUCIBLE_MESSAGE,
    RESERVE_MESSAGE,
)
from local_agent_runtime.contracts import (
    Capabilities,
    CompletionResult,
    ContextWindow,
    HealthStatus,
    Invocation,
    Limits,
    Message,
    ModelDiscovery,
    ModelProfile,
    ProcessingClass,
    ProviderConnection,
    ProviderHealth,
    ReasoningEffort,
    RuntimeConfiguration,
    SessionStatus,
    ToolDefinition,
    ToolRequest,
    ToolResult,
)
from local_agent_runtime.embeddings import EmbeddingProfile, EmbeddingResult, EmbeddingService
from local_agent_runtime.errors import RuntimeFailure
from local_agent_runtime.gateway import create_app
from local_agent_runtime.ports import TextDeltaSink
from local_agent_runtime.providers import build_provider
from local_agent_runtime.service import RuntimeService
from local_agent_runtime.version import PACKAGE_VERSION

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
    capabilities = Capabilities(reasoning_effort_control=True)

    def __init__(self, connection: ProviderConnection, profile: ModelProfile) -> None:
        self.connection = connection
        self.profile = profile
        self.result = CompletionResult("answer", (), "model")
        self.invocations: list[Invocation] = []
        self.delay_seconds = 0.0
        self.health_delay = 0.0
        self.health_failure: RuntimeFailure | None = None
        self.discovery_hangs = False
        self.probes_forbidden = False
        self.health_calls = 0
        self.discovery_calls = 0
        self.confirm_effort = True
        self.efforts: tuple[ReasoningEffort, ...] = (
            ReasoningEffort.LOW,
            ReasoningEffort.HIGH,
        )
        self.context_tokens: int | None = None
        self.context_probe_hangs = False
        self.context_probe_failure: Exception | None = None
        self.context_probes = 0
        self.context_raw: object = None
        # The exact-sizing port: the larger of the two shipped request shapes, or a
        # scripted value (an invalid one proves the contract is checked).
        self.prompt_size: object = None

    def prompt_chars(self, invocation: Invocation) -> int:
        if self.prompt_size is not None:
            return self.prompt_size  # type: ignore[return-value]
        return max(
            chat_prompt_chars(self.profile, invocation, stream=True), cli_prompt_chars(invocation)
        )

    @property
    def reasoning_efforts(self) -> tuple[ReasoningEffort, ...]:
        return self.efforts

    async def context_window(self) -> object:
        self.context_probes += 1
        if self.context_probe_failure is not None:
            raise self.context_probe_failure
        if self.context_probe_hangs:
            await asyncio.sleep(3600)
        if self.context_raw is not None:
            return self.context_raw
        if self.context_tokens is None:
            return ContextWindow()
        return ContextWindow(self.context_tokens, "provider_loaded")

    async def discover_models(self) -> ModelDiscovery:
        assert not self.probes_forbidden, "A plain catalog must stay offline"
        self.discovery_calls += 1
        if self.discovery_hangs:
            await asyncio.sleep(3600)
        return ModelDiscovery(supported=True, models=("model", "other-model"))

    async def health(self) -> ProviderHealth:
        assert not self.probes_forbidden, "A plain catalog must stay offline"
        self.health_calls += 1
        if self.health_delay:
            await asyncio.sleep(self.health_delay)
        if self.health_failure is not None:
            raise self.health_failure
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
        if invocation.reasoning_effort is None or not self.confirm_effort:
            return self.result
        return replace(self.result, effective_reasoning_effort=invocation.reasoning_effort)


class StreamingFakeProvider(FakeProvider):
    capabilities = Capabilities(token_streaming=True, reasoning_effort_control=True)

    def __init__(self, connection: ProviderConnection, profile: ModelProfile) -> None:
        super().__init__(connection, profile)
        self.deltas: list[str] = ["answer"]
        self.streaming_calls = 0
        self.block: asyncio.Event | None = None
        self.streaming_failure: RuntimeFailure | None = None

    async def complete_streaming(
        self, invocation: Invocation, emit_text: TextDeltaSink
    ) -> CompletionResult:
        self.streaming_calls += 1
        self.invocations.append(invocation)
        if self.streaming_failure is not None:
            raise self.streaming_failure
        for delta in self.deltas:
            await emit_text(delta)
        if self.block is not None:
            await self.block.wait()
        return self.result


class MissingStreamingMethodProvider(FakeProvider):
    capabilities = Capabilities(token_streaming=True)


def streaming_runtime(
    tmp_path: Path, limits: Limits | None = None
) -> tuple[RuntimeService, StreamingFakeProvider]:
    connection = ProviderConnection(
        "local", "lmstudio", ProcessingClass.LOCAL, endpoint="http://127.0.0.1:1234/v1"
    )
    profile = ModelProfile("reason", "local", "model", False, True, limits or Limits())
    config = RuntimeConfiguration(
        {"local": connection}, {"reason": profile}, "reason", {"answer": "reason"}
    )
    fake = StreamingFakeProvider(connection, profile)
    return RuntimeService(
        config, SelectionStore(tmp_path / "state"), provider_factory=lambda *_: fake
    ), fake


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


def test_streaming_provider_emits_ordered_coalesced_text_before_completion(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        app, fake = streaming_runtime(tmp_path)
        fake.deltas = ["a", "b" * 64, "c" * 64]
        fake.result = CompletionResult("a" + "b" * 64 + "c" * 64, (), "model")
        created = await app.create_session("hello")
        settled = await app.wait(created["id"])
        assert settled["status"] == "completed"
        events = app.events(created["id"])
        deltas = [event for event in events if event["type"] == "assistant_text_delta"]
        assert [event["payload"] for event in deltas] == [
            {"round": 1, "delta": "a"},
            {"round": 1, "delta": "b" * 64 + "c" * 64},
        ]
        assert "assistant_text_delta" in [event["type"] for event in events]
        assert [event["type"] for event in events][-1] == "session_completed"
        assert "".join(event["payload"]["delta"] for event in deltas) == settled["final_text"]

    asyncio.run(run())


def test_streaming_mismatch_fails_without_fabricating_completion(tmp_path: Path) -> None:
    async def run() -> None:
        app, fake = streaming_runtime(tmp_path)
        fake.deltas = ["partial"]
        fake.result = CompletionResult("different", (), "model")
        created = await app.create_session("hello")
        settled = await app.wait(created["id"])
        assert settled["status"] == "failed"
        assert settled["final_text"] is None
        assert settled["failure"]["code"] == "invalid_provider_response"
        assert [event["type"] for event in app.events(created["id"])] == [
            "session_created",
            "provider_started",
            "assistant_text_delta",
            "session_failed",
        ]

    asyncio.run(run())


def test_context_window_failure_keeps_only_the_stable_public_classification(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        app, fake = streaming_runtime(tmp_path)
        fake.streaming_failure = RuntimeFailure(
            "context_window_exceeded",
            "The request exceeds the selected model's available context window",
        )
        created = await app.create_session("hello")
        settled = await app.wait(created["id"])
        assert settled["status"] == "failed"
        assert settled["failure"] == {
            "code": "context_window_exceeded",
            "message": "The request exceeds the selected model's available context window",
        }
        failure_event = app.events(created["id"])[-1]
        assert failure_event["sequence"] == 3
        assert failure_event["type"] == "session_failed"
        assert failure_event["payload"] == settled["failure"]

    asyncio.run(run())


def test_lm_studio_context_window_sse_composes_to_redacted_runtime_failure(
    tmp_path: Path,
) -> None:
    native_detail = {
        "error": {
            "code": 400,
            "message": (
                "request (40536 tokens) exceeds the available context size (32768 tokens), "
                "try increasing it"
            ),
            "type": "exceed_context_size_error",
            "n_prompt_tokens": 40536,
            "n_ctx": 32768,
        }
    }
    native_message = (
        f"Engine protocol predict request returned 400: {json.dumps(native_detail)}"
        ". Error Data: n/a, Additional Data: n/a"
    )
    payload = {"error": {"message": native_message}, "message": native_message}
    content = f"event: error\ndata: {json.dumps(payload)}\n\n".encode()

    def client_factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    headers={"Content-Type": "text/event-stream"},
                    content=content,
                )
            )
        )

    async def run() -> None:
        connection = ProviderConnection(
            "local", "lmstudio", ProcessingClass.LOCAL, endpoint="http://127.0.0.1:1234/v1"
        )
        profile = ModelProfile("reason", "local", "model", False, True)
        config = RuntimeConfiguration(
            {"local": connection}, {"reason": profile}, "reason", {"answer": "reason"}
        )
        app = RuntimeService(
            config,
            SelectionStore(tmp_path / "state"),
            provider_factory=lambda found_connection, found_profile: LMStudioAdapter(
                found_connection, found_profile, client_factory
            ),
        )
        created = await app.create_session("hello")
        settled = await app.wait(created["id"])
        events = app.events(created["id"])
        assert settled["failure"] == {
            "code": "context_window_exceeded",
            "message": "The request exceeds the selected model's available context window",
        }
        assert events[-1]["type"] == "session_failed"
        assert events[-1]["payload"] == settled["failure"]
        assert "40536" not in json.dumps({"session": settled, "events": events})
        assert "32768" not in json.dumps({"session": settled, "events": events})

    asyncio.run(run())


def test_structured_output_uses_non_streaming_completion_path(tmp_path: Path) -> None:
    async def run() -> None:
        app, fake = streaming_runtime(tmp_path)
        fake.result = CompletionResult('{"value":1}', (), "model")
        created = await app.create_session(
            "hello",
            output_schema={
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
                "additionalProperties": False,
            },
        )
        settled = await app.wait(created["id"])
        assert settled["status"] == "completed"
        assert fake.streaming_calls == 0
        assert all(event["type"] != "assistant_text_delta" for event in app.events(created["id"]))

    asyncio.run(run())


@pytest.mark.parametrize("summary_chars,status", [(200, "completed"), (201, "failed")])
def test_lm_studio_reasoning_channel_answer_is_validated_against_the_original_schema(
    tmp_path: Path, summary_chars: int, status: str
) -> None:
    output_schema = {
        "type": "object",
        "properties": {
            "status": {"const": "ready"},
            "summary": {"type": "string", "maxLength": 200},
        },
        "required": ["status", "summary"],
        "additionalProperties": False,
    }
    answer = json.dumps({"status": "ready", "summary": "s" * summary_chars})
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(404)
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "model",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "", "reasoning_content": answer},
                    }
                ],
            },
        )

    async def run() -> None:
        connection = ProviderConnection(
            "local", "lmstudio", ProcessingClass.LOCAL, endpoint="http://127.0.0.1:1234/v1"
        )
        profile = ModelProfile("reason", "local", "model", False, True)
        config = RuntimeConfiguration(
            {"local": connection}, {"reason": profile}, "reason", {"answer": "reason"}
        )
        app = RuntimeService(
            config,
            SelectionStore(tmp_path / "state"),
            provider_factory=lambda found_connection, found_profile: LMStudioAdapter(
                found_connection,
                found_profile,
                lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            ),
        )
        created = await app.create_session("hello", output_schema=output_schema)
        settled = await app.wait(created["id"])
        assert settled["status"] == status
        assert len(bodies) == 1
        assert bodies[0]["stream"] is False
        assert bodies[0]["response_format"]["json_schema"]["schema"] == output_schema
        if status == "completed":
            assert settled["final_text"] == answer
        else:
            assert settled["failure"]["code"] == "schema_validation_failed"
            assert settled["final_text"] is None

    asyncio.run(run())


def test_cancel_after_streamed_text_preserves_delta_without_completion(tmp_path: Path) -> None:
    async def run() -> None:
        app, fake = streaming_runtime(tmp_path)
        fake.deltas = ["partial"]
        fake.block = asyncio.Event()
        created = await app.create_session("hello")
        for _ in range(100):
            if any(event["type"] == "assistant_text_delta" for event in app.events(created["id"])):
                break
            await asyncio.sleep(0)
        canceled = await app.cancel(created["id"])
        assert canceled["status"] == "canceled"
        assert canceled["final_text"] is None
        assert [event["type"] for event in app.events(created["id"])] == [
            "session_created",
            "provider_started",
            "assistant_text_delta",
            "session_canceled",
        ]

    asyncio.run(run())


@pytest.mark.parametrize(
    ("deltas", "expected_code"),
    [
        ([""], "invalid_provider_response"),
        (["x" * 1_001], "output_limit_exceeded"),
    ],
)
def test_streaming_service_rejects_invalid_or_cumulatively_oversized_deltas(
    tmp_path: Path, deltas: list[str], expected_code: str
) -> None:
    async def run() -> None:
        app, fake = streaming_runtime(tmp_path, Limits(max_output_chars=1_000))
        fake.deltas = deltas
        fake.result = CompletionResult("".join(deltas), (), "model")
        created = await app.create_session("hello")
        settled = await app.wait(created["id"])
        assert settled["status"] == "failed"
        assert settled["failure"]["code"] == expected_code
        assert app.events(created["id"])[-1]["type"] == "session_failed"

    asyncio.run(run())


def test_streaming_capability_without_method_fails_explicitly(tmp_path: Path) -> None:
    async def run() -> None:
        connection = ProviderConnection(
            "local", "lmstudio", ProcessingClass.LOCAL, endpoint="http://127.0.0.1:1234/v1"
        )
        profile = ModelProfile("reason", "local", "model", False, True)
        config = RuntimeConfiguration(
            {"local": connection}, {"reason": profile}, "reason", {"answer": "reason"}
        )
        fake = MissingStreamingMethodProvider(connection, profile)
        app = RuntimeService(
            config, SelectionStore(tmp_path / "state"), provider_factory=lambda *_: fake
        )
        created = await app.create_session("hello")
        settled = await app.wait(created["id"])
        assert settled["status"] == "failed"
        assert settled["failure"]["code"] == "invalid_provider_contract"
        assert [event["type"] for event in app.events(created["id"])] == [
            "session_created",
            "provider_started",
            "session_failed",
        ]

    asyncio.run(run())


def test_streaming_round_counts_tool_and_follow_up_provider_calls(tmp_path: Path) -> None:
    async def run() -> None:
        app, fake = streaming_runtime(tmp_path)
        fake.deltas = ["checking"]
        fake.result = CompletionResult(
            "checking", (ToolRequest("one", "lookup", {"id": 1}),), "model"
        )
        created = await app.create_session("first", [TOOL])
        waiting = await app.wait(created["id"])
        assert waiting["status"] == "waiting_for_tool"

        fake.deltas = ["tool answer"]
        fake.result = CompletionResult("tool answer", (), "model")
        await app.submit_tool_results(created["id"], [ToolResult("one", "lookup", {"value": 1})])
        await app.wait(created["id"])

        fake.deltas = ["follow-up"]
        fake.result = CompletionResult("follow-up", (), "model")
        await app.continue_session(created["id"], "second")
        settled = await app.wait(created["id"])
        assert settled["status"] == "completed"
        deltas = [
            event["payload"]
            for event in app.events(created["id"])
            if event["type"] == "assistant_text_delta"
        ]
        assert deltas == [
            {"round": 1, "delta": "checking"},
            {"round": 2, "delta": "tool answer"},
            {"round": 3, "delta": "follow-up"},
        ]

    asyncio.run(run())


def test_maximum_configured_stream_output_preserves_terminal_event(tmp_path: Path) -> None:
    async def run() -> None:
        app, fake = streaming_runtime(tmp_path, Limits(max_output_chars=100_000))
        fake.deltas = ["x"] * 100_000
        fake.result = CompletionResult("x" * 100_000, (), "model")
        created = await app.create_session("hello")
        settled = await app.wait(created["id"])
        events = app.events(created["id"])
        delta_events = [event for event in events if event["type"] == "assistant_text_delta"]
        assert settled["status"] == "failed"
        assert settled["failure"]["code"] == "output_limit_exceeded"
        assert len(delta_events) <= service_module.STREAM_EVENT_BUDGET
        assert len(events) <= service_module.MAX_EVENTS
        assert events[-1]["type"] == "session_failed"
        assert "".join(event["payload"]["delta"] for event in delta_events) == "x" * 100_000

    asyncio.run(run())


def test_cancel_does_not_overwrite_completion_at_the_exact_event_limit(tmp_path: Path) -> None:
    async def run() -> None:
        app, _ = streaming_runtime(tmp_path)
        created = await app.create_session("hello")
        await app.wait(created["id"])
        record = app.sessions[created["id"]]
        seed = record.events[0]
        record.events = [
            replace(seed, sequence=index, type="provider_started")
            for index in range(1, service_module.MAX_EVENTS)
        ]
        record.status = SessionStatus.RUNNING
        record.finished_at = None

        class CompletionRaceTask:
            def done(self) -> bool:
                record.status = SessionStatus.COMPLETED
                record.add_event("session_completed", {"text": "answer"})
                return True

        record.task = cast("asyncio.Task[None]", CompletionRaceTask())
        settled = await app.cancel(created["id"])
        assert settled["status"] == "completed"
        assert len(record.events) == service_module.MAX_EVENTS
        assert record.events[-1].type == "session_completed"

    asyncio.run(run())


def assert_schema(name: str, data: object) -> None:
    schema = {**SCHEMAS[name], "components": {"schemas": SCHEMAS}}
    Draft202012Validator(schema).validate(data)


def test_shared_conformance_fixture() -> None:
    fixture = json.loads(
        (Path(__file__).resolve().parents[1] / "contracts/conformance.json").read_text()
    )
    assert_schema("ErrorResponse", fixture["error"])
    assert_schema("EventsResponse", {"events": fixture["events"]})
    assert [event["sequence"] for event in fixture["events"]] == [1, 2, 3]
    assert fixture["events"][1]["type"] == "assistant_text_delta"
    assert fixture["events"][1]["payload"] == {"round": 1, "delta": "Grüße"}


def test_gateway_heartbeats_keep_idle_stream_alive_without_runtime_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        service, fake = runtime(tmp_path)
        fake.delay_seconds = 0.16
        monkeypatch.setattr(gateway_module, "SSE_HEARTBEAT_SECONDS", 0.01)
        app = create_app(service, bearer_token=TOKEN)
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            created = await service.create_session("Slow synthetic provider")
            response = await client.get(
                f"/v1/sessions/{created['id']}/events",
                headers={"Authorization": f"Bearer {TOKEN}", "Accept": "text/event-stream"},
            )
            assert response.status_code == 200
            assert response.text.count(": heartbeat\n\n") >= 2
            events = [
                json.loads(line.removeprefix("data: "))
                for line in response.text.splitlines()
                if line.startswith("data: ")
            ]
            assert events == service.events(created["id"])
            assert [event["sequence"] for event in events] == [1, 2, 3]
            assert [event["type"] for event in events] == [
                "session_created",
                "provider_started",
                "session_completed",
            ]
            # A settled session ends promptly without adding heartbeat-only work.
            settled = await client.get(
                f"/v1/sessions/{created['id']}/events?after=3",
                headers={"Authorization": f"Bearer {TOKEN}", "Accept": "text/event-stream"},
            )
            assert settled.text == ""

    asyncio.run(run())


def test_gateway_sse_preserves_assistant_delta_sequence_and_cursor(tmp_path: Path) -> None:
    async def run() -> None:
        service, fake = streaming_runtime(tmp_path)
        fake.deltas = ["first", " second"]
        fake.result = CompletionResult("first second", (), "model")
        app = create_app(service, bearer_token=TOKEN)
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            created = await service.create_session("hello")
            await service.wait(created["id"])
            response = await client.get(
                f"/v1/sessions/{created['id']}/events?after=2",
                headers={"Authorization": f"Bearer {TOKEN}", "Accept": "text/event-stream"},
            )
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert [event["sequence"] for event in events] == [3, 4, 5]
        assert events[0]["type"] == "assistant_text_delta"
        assert events[0]["payload"] == {"round": 1, "delta": "first"}
        assert events[1]["payload"] == {"round": 1, "delta": " second"}
        assert events[2]["type"] == "session_completed"

    asyncio.run(run())


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


def test_tool_request_identity_may_be_reused_on_a_later_turn(tmp_path: Path) -> None:
    """LAR-012: identifiers are scoped to the turn, not the session."""

    async def run() -> None:
        app, fake = runtime(tmp_path)
        fake.result = CompletionResult("", (ToolRequest("call_0", "lookup", {"id": 1}),), "model")
        created = await app.create_session("first", [TOOL])
        assert (await app.wait(created["id"]))["status"] == "waiting_for_tool"
        fake.result = CompletionResult("answer one", (), "model")
        await app.submit_tool_results(created["id"], [ToolResult("call_0", "lookup", {"v": 1})])
        assert (await app.wait(created["id"]))["status"] == "completed"

        fake.result = CompletionResult("", (ToolRequest("call_0", "lookup", {"id": 2}),), "model")
        await app.continue_session(created["id"], "second")
        assert (await app.wait(created["id"]))["status"] == "waiting_for_tool"
        fake.result = CompletionResult("answer two", (), "model")
        await app.submit_tool_results(created["id"], [ToolResult("call_0", "lookup", {"v": 2})])
        settled = await app.wait(created["id"])
        assert settled["status"] == "completed"
        received = [
            event for event in app.events(created["id"]) if event["type"] == "tool_results_received"
        ]
        assert [event["payload"]["count"] for event in received] == [1, 1]

    asyncio.run(run())


def test_duplicate_tool_request_identity_in_one_response_fails(tmp_path: Path) -> None:
    async def run() -> None:
        app, fake = runtime(tmp_path)
        fake.result = CompletionResult(
            "",
            (
                ToolRequest("call_0", "lookup", {"id": 1}),
                ToolRequest("call_0", "lookup", {"id": 2}),
            ),
            "model",
        )
        created = await app.create_session("hello", [TOOL])
        final = await app.wait(created["id"])
        assert final["status"] == "failed"
        assert final["failure"]["code"] == "invalid_tool_request"

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


@pytest.mark.parametrize("agent_summary", ["", "x" * 2_001])
def test_application_validation_retains_lm_studio_string_length_contract(
    tmp_path: Path, agent_summary: str
) -> None:
    async def run() -> None:
        app, fake = streaming_runtime(tmp_path)
        tool = ToolDefinition(
            "operation_finalize",
            "Finalize the operation",
            {
                "type": "object",
                "properties": {
                    "agent_summary": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 2_000,
                    }
                },
                "required": ["agent_summary"],
                "additionalProperties": False,
            },
        )
        fake.deltas = []
        fake.result = CompletionResult(
            "",
            (ToolRequest("finalize", "operation_finalize", {"agent_summary": agent_summary}),),
            "model",
        )
        created = await app.create_session("hello", [tool])
        settled = await app.wait(created["id"])
        assert settled["status"] == "failed"
        assert settled["failure"]["code"] == "invalid_tool_arguments"

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
                "package_version": PACKAGE_VERSION,
                "api_version": API_VERSION,
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


def test_catalog_separates_reasoning_readiness_discovery_and_qualification(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        service, fake = runtime(tmp_path)
        plain = await service.profile_state()
        entry = plain["profiles"][0]
        assert entry["reasoning"] == {"efforts": ["low", "high"], "default": None}
        assert "health" not in entry and "discovery" not in entry
        assert fake.health_calls == 0 and fake.discovery_calls == 0

        full = await service.profile_state(include_health=True, include_discovery=True)
        entry = full["profiles"][0]
        assert entry["health"]["status"] == "available"
        assert entry["discovery"] == {
            "supported": True,
            "models": ["model", "other-model"],
            "detail_code": None,
        }
        # Readiness never implies enumeration and neither implies task fitness.
        assert entry["qualification"] == {"status": "unqualified", "tasks": []}
        assert_schema("ProfilesResponse", full)

    asyncio.run(run())


def test_supported_effort_reaches_the_provider_and_is_reported(tmp_path: Path) -> None:
    async def run() -> None:
        service, fake = runtime(tmp_path)
        created = await service.create_session("question", reasoning_effort="high")
        assert created["requested_reasoning_effort"] == "high"
        settled = await service.wait(created["id"])
        assert fake.invocations[0].reasoning_effort is ReasoningEffort.HIGH
        # Effective effort is provider-reported provenance, never an echo.
        assert settled["effective_reasoning_effort"] == "high"
        fake.confirm_effort = False
        again = await service.wait(
            (await service.create_session("question", reasoning_effort="high"))["id"]
        )
        assert again["requested_reasoning_effort"] == "high"
        assert again["effective_reasoning_effort"] is None
        assert_schema("SessionResponse", settled)

    asyncio.run(run())


def test_unsupported_effort_fails_before_the_provider_is_reached(tmp_path: Path) -> None:
    async def run() -> None:
        service, fake = runtime(tmp_path)
        for value in ("medium", "not-an-effort", 5):
            with pytest.raises(RuntimeFailure) as failure:
                await service.create_session("question", reasoning_effort=value)
            assert failure.value.code in {"reasoning_effort_unsupported", "invalid_request"}
        assert fake.invocations == []
        assert service.sessions == {}

    asyncio.run(run())


def test_no_effort_clients_keep_their_behavior(tmp_path: Path) -> None:
    async def run() -> None:
        service, fake = runtime(tmp_path)
        settled = await service.wait((await service.create_session("question"))["id"])
        assert fake.invocations[0].reasoning_effort is None
        assert settled["requested_reasoning_effort"] is None
        assert settled["effective_reasoning_effort"] is None

    asyncio.run(run())


def test_configured_default_effort_applies_without_a_request(tmp_path: Path) -> None:
    async def run() -> None:
        service, fake = runtime(tmp_path)
        cast("dict[str, ModelProfile]", service.configuration.profiles)["reason"] = replace(
            service.configuration.profiles["reason"],
            default_reasoning_effort=ReasoningEffort.LOW,
        )
        settled = await service.wait((await service.create_session("question"))["id"])
        assert fake.invocations[0].reasoning_effort is ReasoningEffort.LOW
        assert settled["requested_reasoning_effort"] == "low"

    asyncio.run(run())


def test_each_turn_snapshots_its_own_effort(tmp_path: Path) -> None:
    async def run() -> None:
        service, fake = runtime(tmp_path)
        created = await service.create_session("first", reasoning_effort="high")
        await service.wait(created["id"])
        await service.continue_session(created["id"], "second", "low")
        second = await service.wait(created["id"])
        assert [item.reasoning_effort for item in fake.invocations] == [
            ReasoningEffort.HIGH,
            ReasoningEffort.LOW,
        ]
        assert second["requested_reasoning_effort"] == "low"

        # A follow-up without an override stays at the effort already in use.
        await service.continue_session(created["id"], "third")
        third = await service.wait(created["id"])
        assert fake.invocations[2].reasoning_effort is ReasoningEffort.LOW
        assert third["requested_reasoning_effort"] == "low"

    asyncio.run(run())


def test_concurrent_turns_keep_distinct_immutable_selections(tmp_path: Path) -> None:
    async def run() -> None:
        service, fake = runtime(tmp_path)
        fake.delay_seconds = 0.05
        high = await service.create_session("one", reasoning_effort="high")
        low = await service.create_session("two", reasoning_effort="low")
        # A later request cannot re-target work that is already in flight.
        with pytest.raises(RuntimeFailure):
            await service.continue_session(high["id"], "again", "low")
        settled = [await service.wait(high["id"]), await service.wait(low["id"])]
        assert [item["requested_reasoning_effort"] for item in settled] == ["high", "low"]
        assert [item["effective_reasoning_effort"] for item in settled] == ["high", "low"]
        assert {item.reasoning_effort for item in fake.invocations} == {
            ReasoningEffort.HIGH,
            ReasoningEffort.LOW,
        }

    asyncio.run(run())


def test_gateway_carries_effort_and_discovery_without_provider_detail(tmp_path: Path) -> None:
    async def run() -> None:
        service, _ = runtime(tmp_path)
        gateway = create_app(service, bearer_token=TOKEN)
        headers = {"Authorization": "Bearer " + TOKEN}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gateway), base_url="http://127.0.0.1:8765"
        ) as client:
            catalog = await client.get("/v1/profiles?health=true&discovery=true", headers=headers)
            assert catalog.status_code == 200
            assert_schema("ProfilesResponse", catalog.json())
            body = catalog.text
            assert "credential" not in body and "endpoint" not in body and "command" not in body

            assert (
                await client.get("/v1/profiles?discovery=maybe", headers=headers)
            ).status_code == 400

            refused = await client.post(
                "/v1/sessions",
                headers=headers,
                json={"prompt": "hello", "reasoning_effort": "max"},
            )
            assert refused.status_code == 400
            assert refused.json()["error"]["code"] == "reasoning_effort_unsupported"

            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={"prompt": "hello", "reasoning_effort": "low"},
            )
            assert created.status_code == 200
            session_id = created.json()["id"]
            await service.wait(session_id)
            follow_up = await client.post(
                f"/v1/sessions/{session_id}/input",
                headers=headers,
                json={"prompt": "again", "reasoning_effort": "high"},
            )
            assert follow_up.status_code == 200
            assert follow_up.json()["requested_reasoning_effort"] == "high"
            assert_schema("SessionResponse", follow_up.json())

    asyncio.run(run())


def test_a_failing_probe_is_per_profile_state_not_a_catalog_outage(tmp_path: Path) -> None:
    async def run() -> None:
        service, fake = runtime(tmp_path)

        fake.health_failure = RuntimeFailure("provider_unavailable", "down", status_code=503)
        fake.discovery_hangs = True
        service_module.PROBE_TIMEOUT_SECONDS = 0.05
        try:
            state = await service.profile_state(include_health=True, include_discovery=True)
        finally:
            service_module.PROBE_TIMEOUT_SECONDS = 20
        entry = state["profiles"][0]
        assert entry["health"]["status"] == "inconclusive"
        assert entry["health"]["detail_code"] == "provider_unavailable"
        assert entry["discovery"] == {
            "supported": True,
            "models": [],
            "detail_code": "probe_timed_out",
        }
        assert_schema("ProfilesResponse", state)

    asyncio.run(run())


def test_the_plain_catalog_never_touches_a_provider(tmp_path: Path) -> None:
    async def run() -> None:
        service, fake = runtime(tmp_path)

        fake.probes_forbidden = True
        state = await service.profile_state()
        assert state["profiles"][0]["reasoning"]["efforts"] == ["low", "high"]

    asyncio.run(run())


def test_probes_run_concurrently_and_share_one_discovery_per_connection(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        connection = ProviderConnection(
            "local", "lmstudio", ProcessingClass.LOCAL, endpoint="http://127.0.0.1:1234/v1"
        )
        profiles = {
            name: ModelProfile(name, "local", name, False, True)
            for name in ("first", "second", "third")
        }
        fake = FakeProvider(connection, profiles["first"])
        fake.health_delay = 0.15
        config = RuntimeConfiguration({"local": connection}, profiles, "first", {})
        service = RuntimeService(
            config, SelectionStore(tmp_path / "state"), provider_factory=lambda *_: fake
        )
        started = asyncio.get_running_loop().time()
        state = await service.profile_state(include_health=True, include_discovery=True)
        elapsed = asyncio.get_running_loop().time() - started

        assert fake.health_calls == 3
        # One connection enumerates once, however many profiles sit on it.
        assert fake.discovery_calls == 1
        assert elapsed < 0.45
        assert all(
            item["discovery"]["models"] == ["model", "other-model"] for item in state["profiles"]
        )

    asyncio.run(run())


def bounded_runtime(
    tmp_path: Path, limits: Limits, context_tokens: int | None
) -> tuple[RuntimeService, FakeProvider]:
    connection = ProviderConnection(
        "local", "lmstudio", ProcessingClass.LOCAL, endpoint="http://127.0.0.1:1234/v1"
    )
    profile = ModelProfile("reason", "local", "model", False, True, limits)
    config = RuntimeConfiguration(
        {"local": connection}, {"reason": profile}, "reason", {"answer": "reason"}
    )
    fake = FakeProvider(connection, profile)
    fake.context_tokens = context_tokens
    return RuntimeService(
        config, SelectionStore(tmp_path / "state"), provider_factory=lambda *_: fake
    ), fake


BOUNDED = Limits(max_output_tokens=100)
LONG_PROMPT = "u" * 300
INSTRUCTIONS = "i" * 30


def test_long_conversation_keeps_instructions_and_a_recent_suffix(tmp_path: Path) -> None:
    async def run() -> None:
        app, fake = bounded_runtime(tmp_path, BOUNDED, 2_000)
        created = await app.create_session(LONG_PROMPT, [TOOL], instructions=INSTRUCTIONS)
        assert (await app.wait(created["id"]))["status"] == "completed"
        for _ in range(11):
            await app.continue_session(created["id"], LONG_PROMPT)
            settled = await app.wait(created["id"])
            assert settled["status"] == "completed"
        record = app.sessions[created["id"]]
        assert len(record.messages) == 1 + 12 * 2, "the retained transcript is complete"
        sent = fake.invocations[-1].messages
        assert sent[0] == record.messages[0] and sent[0].role == "system"
        # The final assistant reply is appended after the call; the window sent
        # is the newest suffix ending at the latest user message.
        assert sent[1:] == tuple(record.messages[-len(sent) : -1])
        assert sent[1].role == "user" and sent[-1].role == "user"
        assert 3 < len(sent) < len(record.messages)
        # Planned before the final assistant reply was appended.
        dropped = len(record.messages) - 1 - len(sent)
        assert settled["context"] == {
            "capacity_tokens": 2_000,
            "capacity_source": "provider_loaded",
            "estimated_prompt_tokens": settled["context"]["estimated_prompt_tokens"],
            "basis": "estimate",
            "reduced": True,
            "dropped_messages": dropped,
            "configured_output_tokens": 100,
            "allocated_output_tokens": 100,
        }
        assert settled["context"]["estimated_prompt_tokens"] <= 2_000 - 100 - 128
        events = app.events(created["id"])
        reductions = [event for event in events if event["type"] == "context_reduced"]
        assert reductions and reductions[-1]["payload"] == {
            "round": 12,
            "dropped_messages": dropped,
            "dropped_turns": dropped // 2,
            "retained_messages": len(sent),
            "estimated_prompt_tokens": settled["context"]["estimated_prompt_tokens"],
            "capacity_tokens": 2_000,
            "budget_tokens": 2_000 - 100 - 128,
            "basis": "estimate",
            "configured_output_tokens": 100,
            "allocated_output_tokens": 100,
        }
        assert "uuu" not in json.dumps(reductions) and "iii" not in json.dumps(reductions)
        assert [event["type"] for event in events[-3:]] == [
            "provider_started",
            "context_reduced",
            "session_completed",
        ]
        assert_schema("SessionResponse", settled)
        assert_schema("EventsResponse", {"events": events})
        assert fake.context_probes == 12

    asyncio.run(run())


def test_current_turn_tool_groups_survive_pruning_without_replay(tmp_path: Path) -> None:
    async def run() -> None:
        app, fake = bounded_runtime(tmp_path, BOUNDED, None)
        created = await app.create_session(LONG_PROMPT, [TOOL], instructions=INSTRUCTIONS)
        await app.wait(created["id"])
        for _ in range(2):
            await app.continue_session(created["id"], LONG_PROMPT)
            await app.wait(created["id"])
        assert all(
            len(item.messages) == index * 2 + 2 for index, item in enumerate(fake.invocations)
        )
        fake.context_tokens = 1_400
        fake.result = CompletionResult("", (ToolRequest("call-1", "lookup", {"id": 1}),), "model")
        waiting = await app.continue_session(created["id"], LONG_PROMPT)
        assert (await app.wait(waiting["id"]))["status"] == "waiting_for_tool"
        fake.result = CompletionResult("answer", (), "model")
        await app.submit_tool_results(
            created["id"], [ToolResult("call-1", "lookup", {"value": "r" * 600})]
        )
        settled = await app.wait(created["id"])
        assert settled["status"] == "completed"
        record = app.sessions[created["id"]]
        assert len(fake.invocations) == 5
        sent = fake.invocations[-1].messages
        assert [message.role for message in sent] == ["system", "user", "assistant", "tool"]
        assert sent[1:] == tuple(record.messages[-4:-1])
        assert sent[2].tool_requests[0].id == "call-1"
        assert sent[3].tool_request_id == "call-1"
        assert settled["pending_tools"] == [] and settled["tool_rounds"] == 1
        events = app.events(created["id"])
        assert sum(event["type"] == "tool_results_received" for event in events) == 1
        assert sum(event["type"] == "tool_requests" for event in events) == 1
        final = [event for event in events if event["type"] == "context_reduced"][-1]
        assert final["payload"]["dropped_turns"] == 3
        assert final["payload"]["retained_messages"] == 4
        assert settled["context"]["dropped_messages"] == 6

    asyncio.run(run())


def test_irreducible_input_fails_before_any_provider_call(tmp_path: Path) -> None:
    async def run() -> None:
        app, fake = bounded_runtime(tmp_path, BOUNDED, 800)
        created = await app.create_session(LONG_PROMPT, [TOOL], instructions=INSTRUCTIONS)
        settled = await app.wait(created["id"])
        assert settled["status"] == "failed"
        assert settled["failure"] == {
            "code": "context_window_exceeded",
            "message": IRREDUCIBLE_MESSAGE,
        }
        assert fake.invocations == []
        assert settled["context"]["capacity_tokens"] == 800
        assert settled["context"]["reduced"] is False
        assert settled["context"]["estimated_prompt_tokens"] > 800 - 100 - 128
        assert [event["type"] for event in app.events(created["id"])] == [
            "session_created",
            "provider_started",
            "session_failed",
        ]
        assert_schema("SessionResponse", settled)

        # A small context first tries a smaller allocation; 2000-128-650 = 1222
        # is under the 2048 floor, so the request is irreducible.
        app, fake = bounded_runtime(tmp_path / "small", Limits(max_output_tokens=4_096), 2_000)
        created = await app.create_session("hello")
        settled = await app.wait(created["id"])
        assert settled["failure"] == {
            "code": "context_window_exceeded",
            "message": IRREDUCIBLE_MESSAGE,
        }
        assert fake.invocations == []
        assert settled["context"]["allocated_output_tokens"] == 4_096
        # A large context never reallocates output and reports the reserve problem.
        app, fake = bounded_runtime(
            tmp_path / "reserve", Limits(max_output_tokens=300_000), 200_000
        )
        created = await app.create_session("hello")
        settled = await app.wait(created["id"])
        assert settled["failure"] == {
            "code": "context_window_exceeded",
            "message": RESERVE_MESSAGE,
        }
        assert fake.invocations == []

    asyncio.run(run())


def test_unknown_capacity_changes_nothing_and_retained_limits_still_apply(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        app, fake = bounded_runtime(tmp_path, BOUNDED, None)
        created = await app.create_session(LONG_PROMPT, [TOOL], instructions=INSTRUCTIONS)
        await app.wait(created["id"])
        for _ in range(11):
            await app.continue_session(created["id"], LONG_PROMPT)
            settled = await app.wait(created["id"])
        record = app.sessions[created["id"]]
        assert fake.invocations[-1].messages == tuple(record.messages[:-1])
        assert settled["context"]["capacity_source"] == "unknown"
        assert settled["context"]["capacity_tokens"] is None
        assert settled["context"]["reduced"] is False
        assert settled["context"]["basis"] == "estimate"
        assert type(settled["context"]["estimated_prompt_tokens"]) is int
        assert not any(event["type"] == "context_reduced" for event in app.events(created["id"]))

    asyncio.run(run())


def _serialized_chars(messages: Sequence[Message]) -> int:
    return len(json.dumps([item.public_dict() for item in messages], separators=(",", ":")))


# A ceiling the pruned window fits but a longer retained transcript outgrows.
CHAR_LIMITS = Limits(max_output_tokens=100, max_input_chars=4_800)


def assert_accepted_by_both_adapters(profile: ModelProfile, invocation: Invocation) -> None:
    """The planned window passes the exact send-time checks of both adapter families."""
    for stream in (False, True):
        body = chat_body(profile, invocation, stream=stream)
        assert len(json.dumps(body, ensure_ascii=False)) <= invocation.limits.max_input_chars
    assert len(cli_bounded_prompt(invocation)) > 0


async def _long_transcript(app: RuntimeService, turns: int) -> dict[str, Any]:
    created = await app.create_session(LONG_PROMPT, instructions=INSTRUCTIONS)
    await app.wait(created["id"])
    settled: dict[str, Any] = created
    for _ in range(turns):
        await app.continue_session(created["id"], LONG_PROMPT)
        settled = await app.wait(created["id"])
        assert settled["status"] == "completed"
    return settled


def test_a_long_retained_transcript_is_pruned_per_call_not_refused_up_front(
    tmp_path: Path,
) -> None:
    """The retained transcript may outgrow the input ceiling; each call is planned to fit.

    Token capacity is roomy and large here, so the character ceiling alone prunes whole
    earlier turns while the configured output allowance stays untouched.
    """

    async def run() -> None:
        app, fake = bounded_runtime(tmp_path, CHAR_LIMITS, 200_000)
        settled = await _long_transcript(app, 12)
        record = app.sessions[settled["id"]]
        assert len(record.messages) == 1 + 13 * 2
        assert _serialized_chars(record.messages) > CHAR_LIMITS.max_input_chars
        # Counts-only truthfulness: the reduction, its capacity evidence and the
        # unchanged output allowance are all reported as they are.
        assert settled["context"]["reduced"] is True
        assert settled["context"]["dropped_messages"] > 0
        assert settled["context"]["capacity_tokens"] == 200_000
        assert settled["context"]["capacity_source"] == "provider_loaded"
        assert settled["context"]["allocated_output_tokens"] == 100
        assert settled["context"]["configured_output_tokens"] == 100
        sent = fake.invocations[-1].messages
        assert sent[0].role == "system" and sent[-1] == record.messages[-2]
        assert 1 < len(sent) < len(record.messages)
        assert any(event["type"] == "context_reduced" for event in app.events(settled["id"]))
        # The planned window is what the adapter would send, and both adapter
        # families accept it under their own unchanged checks.
        assert_accepted_by_both_adapters(fake.profile, fake.invocations[-1])

        # Incoming input keeps its own fail-closed ceiling: one over-long follow-up is
        # refused before anything is retained or scheduled.
        before = list(record.messages)
        with pytest.raises(RuntimeFailure) as caught:
            await app.continue_session(settled["id"], "u" * (CHAR_LIMITS.max_input_chars + 1))
        assert caught.value.code == "invalid_request"
        assert record.messages == before
        assert app.session(settled["id"])["status"] == "completed"
        with pytest.raises(RuntimeFailure) as caught:
            await app.create_session("u" * (CHAR_LIMITS.max_input_chars + 1))
        assert caught.value.code == "invalid_request"
        # The opening turn is irreducible input as a whole: instructions plus prompt.
        with pytest.raises(RuntimeFailure) as caught:
            await app.create_session(
                "u" * (CHAR_LIMITS.max_input_chars - 100), instructions="i" * 200
            )
        assert caught.value.code == "input_limit_exceeded"

    asyncio.run(run())


def test_unknown_capacity_still_prunes_under_character_pressure(tmp_path: Path) -> None:
    """Without capacity evidence nothing is claimed about the model, but the profile's
    character ceiling still selects whole earlier turns out so the call can be made."""

    async def run() -> None:
        app, fake = bounded_runtime(tmp_path, CHAR_LIMITS, None)
        settled = await _long_transcript(app, 12)
        record = app.sessions[settled["id"]]
        assert _serialized_chars(record.messages) > CHAR_LIMITS.max_input_chars
        assert settled["context"]["reduced"] is True
        assert settled["context"]["dropped_messages"] > 0
        assert settled["context"]["capacity_tokens"] is None
        assert settled["context"]["capacity_source"] == "unknown"
        assert settled["context"]["basis"] == "estimate"
        assert settled["context"]["allocated_output_tokens"] == 100
        sent = fake.invocations[-1].messages
        assert sent[0].role == "system" and sent[-1] == record.messages[-2]
        assert len(sent) < len(record.messages)
        assert_accepted_by_both_adapters(fake.profile, fake.invocations[-1])
        reductions = [e for e in app.events(settled["id"]) if e["type"] == "context_reduced"]
        assert reductions and reductions[-1]["payload"]["capacity_tokens"] is None

    asyncio.run(run())


# Two synthetic consumer contexts of deliberately different shape and vocabulary: a
# structured skill/action record and a prose procedure. Both are padded to one length
# so the only way their plans could differ is content-based branching, which the
# runtime must not have. Neither names a real resource, provider or product.
_STRUCTURED_CONTEXT = json.dumps(
    {
        "skill": "alpha-holding",
        "fields": [{"name": "quantity", "unit": "gram"}, {"name": "purity", "unit": "permille"}],
        "action": {"id": "act-alpha-1", "state": "pending", "awaiting": "quantity"},
    },
    separators=(",", ":"),
)
_PROSE_CONTEXT = (
    "Skill beta-account: ask for the account nickname, then confirm its currency "
    "before staging anything. Active action act-beta-7 is waiting for the balance answer."
)
_CONTEXT_WIDTH = max(len(_STRUCTURED_CONTEXT), len(_PROSE_CONTEXT))
OPAQUE_CONTEXTS = {
    "structured-skill": (
        _STRUCTURED_CONTEXT.ljust(_CONTEXT_WIDTH),
        ("alpha-holding", "act-alpha-1"),
    ),
    "prose-procedure": (_PROSE_CONTEXT.ljust(_CONTEXT_WIDTH), ("beta-account", "act-beta-7")),
}
# Filler turns between the refreshed contexts: enough that a bounded window keeps
# neither stale copy of the context, so only the current turn's copy can survive.
OPAQUE_FILLER_TURNS = 10
# Each ceiling sits about half a filler turn away from the nearest pruning boundary
# for both contexts, so equal treatment is not an accident of one shape's framing.
OPAQUE_CAPACITIES = {
    "known-capacity": (BOUNDED, 2_000),
    "unknown-capacity": (Limits(max_output_tokens=100, max_input_chars=3_800), None),
}


def _opaque_prompts(context: str) -> tuple[str, str, str]:
    """The consumer supplies its complete context on every turn that needs it."""
    return (
        f"{context}\n\nWhat is pending?",
        f"{context}\n\nStill pending?",
        f"{context}\n\nAnything else?",
    )


async def _opaque_journey(
    tmp_path: Path, limits: Limits, capacity: int | None, context: str
) -> tuple[list[tuple[Any, ...]], RuntimeService, FakeProvider]:
    """Initial, continued and reduced turns carrying one consumer context.

    Returns the trace of how each provider call was planned: the roles sent, the
    public context counts and the reduction events. Nothing in it depends on the
    consumer's wording, so two contexts of the same size must produce equal traces.
    """
    app, fake = bounded_runtime(tmp_path, limits, capacity)
    initial, continued, reduced = _opaque_prompts(context)
    created = await app.create_session(initial, [TOOL], instructions=INSTRUCTIONS)
    session_id = created["id"]
    trace: list[tuple[Any, ...]] = []

    async def step(name: str, prompt: str) -> None:
        events_before = len(app.events(session_id))
        settled = await app.wait(session_id)
        assert settled["status"] == "completed", settled["failure"]
        sent = fake.invocations[-1]
        assert sent.messages[0].content == INSTRUCTIONS and sent.messages[0].role == "system"
        assert sent.messages[-1].role == "user" and sent.messages[-1].content == prompt
        assert sent.tools == app.sessions[session_id].tools, "permitted tools are unchanged"
        reductions = [
            event["payload"]
            for event in app.events(session_id)[events_before:]
            if event["type"] == "context_reduced"
        ]
        trace.append(
            (
                name,
                tuple(message.role for message in sent.messages),
                dict(settled["context"]),
                reductions,
            )
        )

    await step("initial", initial)
    await app.continue_session(session_id, continued)
    await step("continued", continued)
    for _ in range(OPAQUE_FILLER_TURNS):
        await app.continue_session(session_id, LONG_PROMPT)
        assert (await app.wait(session_id))["status"] == "completed"
    await app.continue_session(session_id, reduced)
    await step("reduced", reduced)
    return trace, app, fake


@pytest.mark.parametrize("capacity_mode", list(OPAQUE_CAPACITIES), ids=list(OPAQUE_CAPACITIES))
@pytest.mark.parametrize("shape", list(OPAQUE_CONTEXTS), ids=list(OPAQUE_CONTEXTS))
def test_consumer_context_is_opaque_and_the_current_copy_survives_reduction(
    tmp_path: Path, shape: str, capacity_mode: str
) -> None:
    """Consumer resource/action context is opaque input the runtime delivers verbatim,
    never reads, never disclosed in counts-only reporting, and never replaced by a
    built-in fallback when it is missing. Its current copy survives every reduction."""

    context, markers = OPAQUE_CONTEXTS[shape]
    limits, capacity = OPAQUE_CAPACITIES[capacity_mode]

    async def run() -> None:
        trace, app, fake = await _opaque_journey(tmp_path, limits, capacity, context)
        initial, continued, reduced = _opaque_prompts(context)
        session_id = next(iter(app.sessions))
        record = app.sessions[session_id]
        # The runtime forwards exactly the consumer's text and its own recorded replies;
        # it adds no instruction, resource name, procedure or fallback of its own.
        supplied = {INSTRUCTIONS, initial, continued, reduced, LONG_PROMPT, "answer"}
        for invocation in fake.invocations:
            assert {message.content for message in invocation.messages} <= supplied
        assert [entry[1] for entry in trace[:2]] == [
            ("system", "user"),
            ("system", "user", "assistant", "user"),
        ]
        assert trace[0][2]["reduced"] is False and trace[1][2]["reduced"] is False
        # Under pressure both stale copies of the context leave the window while the
        # current turn's copy and the instructions are kept; nothing is re-executed.
        name, roles, public_context, reductions = trace[2]
        assert name == "reduced" and public_context["reduced"] is True
        assert public_context["dropped_messages"] >= 4 and reductions
        assert public_context["capacity_tokens"] == capacity
        assert public_context["capacity_source"] == (
            "unknown" if capacity is None else "provider_loaded"
        )
        assert roles[0] == "system" and roles[-1] == "user" and len(roles) < len(record.messages)
        final = fake.invocations[-1].messages
        assert [message.content for message in final].count(reduced) == 1
        assert initial not in {message.content for message in final}
        assert continued not in {message.content for message in final}
        assert final[-1].content == reduced, "the refreshed context arrives intact"
        assert_accepted_by_both_adapters(fake.profile, fake.invocations[-1])
        # Counts only: neither the public session nor any event carries the consumer's
        # context, and the provider/model route is unchanged by the reduction.
        settled = app.session(session_id)
        disclosed = json.dumps([settled, app.events(session_id)])
        assert not any(marker in disclosed for marker in markers)
        assert settled["provider_id"] == "local" and settled["effective_model"] == "model"
        assert len(record.messages) == 1 + (3 + OPAQUE_FILLER_TURNS) * 2
        # Missing context is refused by the public contract and nothing is invented.
        before = list(record.messages)
        with pytest.raises(RuntimeFailure) as caught:
            await app.continue_session(session_id, "   ")
        assert caught.value.code == "invalid_request"
        assert record.messages == before

    asyncio.run(run())


@pytest.mark.parametrize("capacity_mode", list(OPAQUE_CAPACITIES), ids=list(OPAQUE_CAPACITIES))
def test_differently_shaped_consumer_contexts_receive_identical_structural_treatment(
    tmp_path: Path, capacity_mode: str
) -> None:
    """Two contexts that share nothing but their size are planned identically on every
    turn: the runtime has no resource-specific branch, wording or policy."""

    limits, capacity = OPAQUE_CAPACITIES[capacity_mode]

    async def run() -> None:
        traces = {}
        for shape, (context, _) in OPAQUE_CONTEXTS.items():
            traces[shape], _, _ = await _opaque_journey(tmp_path / shape, limits, capacity, context)
        structured, prose = traces.values()
        assert structured == prose
        assert structured[-1][2]["reduced"] is True

    asyncio.run(run())


def test_an_irreducible_current_turn_fails_explicitly_without_a_provider_call(
    tmp_path: Path,
) -> None:
    """A current turn that cannot fit the character ceiling on its own is refused as
    input_limit_exceeded by the plan: the provider is not called, nothing is dropped
    silently, and the transcript stays retained."""

    async def run() -> None:
        app, fake = bounded_runtime(tmp_path, CHAR_LIMITS, 200_000)
        fake.result = CompletionResult("", (ToolRequest("one", "lookup", {"id": 1}),), "model")
        created = await app.create_session(LONG_PROMPT, [TOOL], instructions=INSTRUCTIONS)
        waiting = await app.wait(created["id"])
        assert waiting["status"] == "waiting_for_tool"
        calls = len(fake.invocations)
        # One tool result within its own limit, but the whole current turn (prompt, the
        # tool request and this result) can no longer fit the ceiling.
        oversized = ToolResult("one", "lookup", {"value": "v" * CHAR_LIMITS.max_input_chars})
        await app.submit_tool_results(created["id"], [oversized])
        failed = await app.wait(created["id"])
        assert failed["status"] == "failed"
        assert failed["failure"]["code"] == "input_limit_exceeded"
        assert failed["failure"]["message"] == INPUT_IRREDUCIBLE_MESSAGE
        assert len(fake.invocations) == calls, "the provider was not called"
        record = app.sessions[created["id"]]
        assert record.messages[-1].role == "tool" and len(record.messages) == 4
        assert failed["context"]["reduced"] is False

    asyncio.run(run())


def test_adapter_serialized_body_limits_remain_the_final_refusal() -> None:
    """Planning never replaces the adapters' own fail-closed check on what they send."""
    profile = ModelProfile("profile", "provider", "model", False, True)
    window = tuple(Message("user", "u" * 300) for _ in range(4))
    accepted = Invocation(window, (), Limits(max_input_chars=4_800))
    assert chat_body(profile, accepted)["messages"][0]["content"] == "u" * 300
    tighter = Invocation(window, (), Limits(max_input_chars=1_000))
    with pytest.raises(RuntimeFailure) as caught:
        chat_body(profile, tighter)
    assert caught.value.code == "input_limit_exceeded"
    with pytest.raises(RuntimeFailure) as caught:
        cli_bounded_prompt(tighter)
    assert caught.value.code == "input_limit_exceeded"


def test_planning_uses_the_adapters_exact_size_so_a_heavier_wire_shape_is_never_admitted(
    tmp_path: Path,
) -> None:
    """A window the public form sizes as fitting can still exceed the chat wire shape
    (tool-call arguments are re-encoded as JSON strings there). Planning measures with
    the adapter's own size, so the sent window is one the adapter accepts."""
    search = ToolDefinition(
        "search",
        "Search a phrase",
        {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
    )

    async def conversation(app: RuntimeService, fake: FakeProvider) -> tuple[str, dict[str, Any]]:
        quotes = '"' * 600
        fake.result = CompletionResult("", (ToolRequest("one", "search", {"q": quotes}),), "model")
        created = await app.create_session("find", [search], instructions=INSTRUCTIONS)
        assert (await app.wait(created["id"]))["status"] == "waiting_for_tool"
        fake.result = CompletionResult("found", (), "model")
        await app.submit_tool_results(created["id"], [ToolResult("one", "search", {"value": 1})])
        assert (await app.wait(created["id"]))["status"] == "completed"
        settled: dict[str, Any] = {}
        for _ in range(6):
            await app.continue_session(created["id"], LONG_PROMPT)
            settled = await app.wait(created["id"])
            assert settled["status"] == "completed"
        return created["id"], settled

    async def run() -> None:
        # Measure the same conversation's two wire shapes under a roomy ceiling first.
        roomy_app, roomy_fake = bounded_runtime(tmp_path / "roomy", BOUNDED, 200_000)
        session_id, roomy_settled = await conversation(roomy_app, roomy_fake)
        assert roomy_settled["context"]["reduced"] is False
        whole = roomy_fake.invocations[-1]
        compact = cli_prompt_chars(whole)
        chat = chat_prompt_chars(roomy_fake.profile, whole, stream=True)
        assert compact < chat, "the chat shape is the heavier one for this transcript"
        limit = (compact + chat) // 2

        limits = Limits(max_output_tokens=100, max_input_chars=limit)
        app, fake = bounded_runtime(tmp_path / "pressured", limits, 200_000)
        session_id, settled = await conversation(app, fake)
        record = app.sessions[session_id]
        pressured_whole = Invocation(tuple(record.messages[:-1]), record.tools, limits)
        # The whole transcript fits the compact public form but not the chat wire shape...
        assert cli_prompt_chars(pressured_whole) <= limit
        assert chat_prompt_chars(fake.profile, pressured_whole, stream=True) > limit
        # ...so the plan dropped turns, and what was sent is accepted by both adapters.
        assert settled["context"]["reduced"] is True
        sent = fake.invocations[-1]
        assert len(sent.messages) < len(pressured_whole.messages)
        assert_accepted_by_both_adapters(fake.profile, sent)

    asyncio.run(run())


def test_an_invalid_adapter_prompt_size_is_a_provider_contract_failure(tmp_path: Path) -> None:
    async def run() -> None:
        app, fake = bounded_runtime(tmp_path, CHAR_LIMITS, 200_000)
        fake.prompt_size = "big"
        created = await app.create_session(LONG_PROMPT, instructions=INSTRUCTIONS)
        failed = await app.wait(created["id"])
        assert failed["status"] == "failed"
        assert failed["failure"]["code"] == "invalid_provider_contract"
        assert fake.invocations == []
        # A heavier-than-estimated adapter size is honored: nothing is sent that it rejects.
        app, fake = bounded_runtime(tmp_path / "heavy", CHAR_LIMITS, 200_000)
        fake.prompt_size = CHAR_LIMITS.max_input_chars + 1
        created = await app.create_session(LONG_PROMPT, instructions=INSTRUCTIONS)
        failed = await app.wait(created["id"])
        assert failed["status"] == "failed"
        assert failed["failure"]["code"] == "input_limit_exceeded"
        assert fake.invocations == []

    asyncio.run(run())


def test_capacity_probe_failure_or_timeout_leaves_capacity_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        app, fake = bounded_runtime(tmp_path, BOUNDED, 2_000)
        fake.context_probe_failure = RuntimeError("native detail")
        created = await app.create_session("hello")
        settled = await app.wait(created["id"])
        assert settled["status"] == "completed"
        assert settled["context"]["capacity_source"] == "unknown"
        assert "native detail" not in json.dumps({"s": settled, "e": app.events(created["id"])})

        monkeypatch.setattr(service_module, "CONTEXT_PROBE_TIMEOUT_SECONDS", 0.05)
        app, fake = bounded_runtime(tmp_path / "hang", BOUNDED, 2_000)
        fake.context_probe_hangs = True
        created = await app.create_session("hello")
        settled = await app.wait(created["id"])
        assert settled["status"] == "completed"
        assert settled["context"]["capacity_source"] == "unknown"
        assert len(fake.invocations) == 1

    asyncio.run(run())


def test_cancel_and_overall_timeout_cover_the_capacity_probe(tmp_path: Path) -> None:
    async def run() -> None:
        app, fake = bounded_runtime(tmp_path, BOUNDED, 2_000)
        fake.context_probe_hangs = True
        created = await app.create_session("hello")
        await asyncio.sleep(0)
        canceled = await app.cancel(created["id"])
        assert canceled["status"] == "canceled"
        assert fake.invocations == []
        assert app.events(created["id"])[-1]["type"] == "session_canceled"

        app, fake = bounded_runtime(tmp_path / "timeout", Limits(timeout_seconds=1), 2_000)
        fake.context_probe_hangs = True
        created = await app.create_session("hello")
        settled = await app.wait(created["id"])
        assert settled["status"] == "failed"
        assert settled["failure"]["code"] == "provider_timeout"
        assert fake.invocations == []

    asyncio.run(run())


def test_streaming_rounds_apply_the_same_window(tmp_path: Path) -> None:
    async def run() -> None:
        app, fake = streaming_runtime(tmp_path, BOUNDED)
        fake.context_tokens = 2_000
        created = await app.create_session(LONG_PROMPT, instructions=INSTRUCTIONS)
        await app.wait(created["id"])
        for _ in range(11):
            await app.continue_session(created["id"], LONG_PROMPT)
            settled = await app.wait(created["id"])
            assert settled["status"] == "completed"
        record = app.sessions[created["id"]]
        sent = fake.invocations[-1].messages
        assert sent[0].role == "system" and len(sent) < len(record.messages)
        assert settled["final_text"] == "answer"
        assert [event["type"] for event in app.events(created["id"])[-4:]] == [
            "provider_started",
            "context_reduced",
            "assistant_text_delta",
            "session_completed",
        ]

    asyncio.run(run())


def test_provider_reported_prompt_size_calibrates_only_well_formed_counts(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        app, fake = bounded_runtime(tmp_path, BOUNDED, 2_000)
        fake.result = CompletionResult("answer", (), "model", usage={"prompt_tokens": 50})
        created = await app.create_session(LONG_PROMPT, instructions=INSTRUCTIONS)
        first = await app.wait(created["id"])
        assert first["context"]["basis"] == "estimate"
        await app.continue_session(created["id"], LONG_PROMPT)
        second = await app.wait(created["id"])
        assert second["context"]["basis"] == "calibrated"
        # 50 observed for the first prompt plus the assistant reply and new prompt.
        assert second["context"]["estimated_prompt_tokens"] == 50 + (2 + 8) + (100 + 8)
        assert fake.invocations[-1].messages == tuple(app.sessions[created["id"]].messages[:-1])

        for usage in ({"prompt_tokens": 0}, {"prompt_tokens": 3.5}, {"prompt_tokens": 1}, {}):
            app, fake = bounded_runtime(tmp_path / str(len(usage)), BOUNDED, 2_000)
            fake.result = CompletionResult("answer", (), "model", usage=usage)
            created = await app.create_session(LONG_PROMPT, instructions=INSTRUCTIONS)
            await app.wait(created["id"])
            assert app.sessions[created["id"]].observed_prompt is None
            await app.continue_session(created["id"], LONG_PROMPT)
            assert (await app.wait(created["id"]))["context"]["basis"] == "estimate"

    asyncio.run(run())


@pytest.mark.parametrize(
    "raw",
    [
        ContextWindow(2_000, "unknown"),
        ContextWindow(2_000, "guessed"),
        ContextWindow(None, "provider_loaded"),
        ContextWindow(0, "provider_loaded"),
        ContextWindow(2_000.0, "provider_loaded"),  # type: ignore[arg-type]
        {"tokens": 2_000, "source": "provider_loaded"},
    ],
    ids=["positive-unknown", "arbitrary-source", "none-loaded", "zero", "float", "not-a-window"],
)
def test_inconsistent_capacity_reports_are_normalized_to_unknown(
    tmp_path: Path, raw: object
) -> None:
    async def run() -> None:
        app, fake = bounded_runtime(tmp_path, BOUNDED, None)
        fake.context_raw = raw
        created = await app.create_session(LONG_PROMPT, instructions=INSTRUCTIONS)
        for _ in range(11):
            await app.wait(created["id"])
            await app.continue_session(created["id"], LONG_PROMPT)
        settled = await app.wait(created["id"])
        assert settled["status"] == "completed"
        assert settled["context"]["capacity_tokens"] is None
        assert settled["context"]["capacity_source"] == "unknown"
        assert settled["context"]["reduced"] is False
        assert len(fake.invocations[-1].messages) == len(app.sessions[created["id"]].messages) - 1
        assert_schema("SessionResponse", settled)

    asyncio.run(run())


def test_output_allocation_is_per_call_and_the_next_roomy_call_regains_it(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        limits = Limits(max_output_tokens=8_192)
        app, fake = bounded_runtime(tmp_path, limits, 32_768)
        instructions = "i" * (4_000 - 8) * 3
        created = await app.create_session("hello", instructions=instructions)
        first = await app.wait(created["id"])
        assert first["status"] == "completed"
        assert fake.invocations[-1].limits.max_output_tokens == 8_192
        assert first["context"]["allocated_output_tokens"] == 8_192
        # A turn whose mandatory set exceeds the 22937 budget by 35 tokens.
        pressure = "p" * ((22_972 - 640 - 4_000 - 8) * 3)
        await app.continue_session(created["id"], pressure)
        second = await app.wait(created["id"])
        assert second["status"] == "completed"
        sent = fake.invocations[-1]
        assert sent.limits.max_output_tokens == 8_157
        assert sent.limits.max_input_chars == limits.max_input_chars
        assert [message.role for message in sent.messages] == ["system", "user"]
        assert second["context"]["configured_output_tokens"] == 8_192
        assert second["context"]["allocated_output_tokens"] == 8_157
        assert second["context"]["reduced"] is True
        assert second["limits"] == {**second["limits"], "max_output_tokens": 8_192}
        assert app.sessions[created["id"]].profile.limits == limits
        events = app.events(created["id"])
        reduction = [event for event in events if event["type"] == "context_reduced"][-1]
        assert reduction["payload"]["configured_output_tokens"] == 8_192
        assert reduction["payload"]["allocated_output_tokens"] == 8_157
        assert reduction["payload"]["dropped_messages"] == 2
        assert "ppp" not in json.dumps(events)
        # A roomy follow-up regains the configured allowance; the oversized
        # earlier turn cannot be retained beside it, the short one can.
        await app.continue_session(created["id"], "short")
        third = await app.wait(created["id"])
        assert third["status"] == "completed"
        assert fake.invocations[-1].limits.max_output_tokens == 8_192
        assert third["context"]["allocated_output_tokens"] == 8_192
        assert third["context"]["reduced"] is True
        assert [message.content[:5] for message in fake.invocations[-1].messages] == [
            "iiiii",
            "short",
        ]
        await app.continue_session(created["id"], "again")
        fourth = await app.wait(created["id"])
        assert fourth["context"]["allocated_output_tokens"] == 8_192
        assert [message.content[:5] for message in fake.invocations[-1].messages] == [
            "iiiii",
            "short",
            "answe",
            "again",
        ]
        assert_schema("SessionResponse", third)
        assert_schema("EventsResponse", {"events": events})

    asyncio.run(run())


def test_output_allocation_reaches_the_wire_and_tool_groups_are_not_re_executed(
    tmp_path: Path,
) -> None:
    bodies: list[dict[str, Any]] = []
    replies: list[dict[str, Any]] = [
        {
            "model": "model",
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {"name": "lookup", "arguments": '{"id": 1}'},
                            }
                        ],
                    },
                }
            ],
        },
        {
            "model": "model",
            "choices": [{"finish_reason": "stop", "message": {"content": '{"ok": true}'}}],
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/models":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "type": "llm",
                            "key": "model",
                            "loaded_instances": [
                                {"id": "model", "config": {"context_length": 32_768}}
                            ],
                        }
                    ]
                },
            )
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=replies[len(bodies) - 1])

    async def run() -> None:
        connection = ProviderConnection(
            "local", "lmstudio", ProcessingClass.LOCAL, endpoint="http://127.0.0.1:1234/v1"
        )
        limits = Limits(max_output_tokens=8_192)
        profile = ModelProfile("reason", "local", "model", False, True, limits)
        config = RuntimeConfiguration(
            {"local": connection}, {"reason": profile}, "reason", {"answer": "reason"}
        )
        app = RuntimeService(
            config,
            SelectionStore(tmp_path / "state"),
            provider_factory=lambda found_connection, found_profile: LMStudioAdapter(
                found_connection,
                found_profile,
                lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            ),
        )
        # Structured output disables streaming here so the JSON replies apply.
        pressure = "p" * ((23_500 - 640 - 8) * 3)
        created = await app.create_session(pressure, [TOOL], output_schema={"type": "object"})
        waiting = await app.wait(created["id"])
        assert waiting["status"] == "waiting_for_tool"
        assert (
            bodies[0]["max_tokens"]
            == 32_768 - 1_639 - waiting["context"]["estimated_prompt_tokens"]
        )
        assert bodies[0]["max_tokens"] < 8_192
        await app.submit_tool_results(
            created["id"], [ToolResult("call-1", "lookup", {"value": "v" * 3_000})]
        )
        settled = await app.wait(created["id"])
        assert settled["status"] == "completed", settled["failure"]
        assert settled["final_text"] == '{"ok": true}'
        # The second call carries the complete tool group once and a smaller
        # allocation because the tool result joined the mandatory set.
        assert [item["role"] for item in bodies[1]["messages"]] == ["user", "assistant", "tool"]
        assert (
            bodies[1]["max_tokens"]
            == 32_768 - 1_639 - settled["context"]["estimated_prompt_tokens"]
        )
        assert bodies[1]["max_tokens"] < bodies[0]["max_tokens"]
        assert len(bodies) == 2
        assert sum(event["type"] == "tool_requests" for event in app.events(created["id"])) == 1
        assert settled["limits"]["max_output_tokens"] == 8_192

    asyncio.run(run())


@pytest.mark.parametrize("streaming", [False, True], ids=["complete", "streamed"])
@pytest.mark.parametrize("with_tool_call", [False, True], ids=["text", "partial-tool-call"])
def test_provider_length_termination_never_completes_or_executes_tools(
    tmp_path: Path, streaming: bool, with_tool_call: bool
) -> None:
    tool_call = {
        "id": "call-1",
        "type": "function",
        "function": {"name": "lookup", "arguments": '{"id": 1'},
    }
    if streaming:
        frames = [
            {"model": "model", "choices": [{"index": 0, "delta": {"content": "partial "}}]},
            {
                "model": "model",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"tool_calls": [{"index": 0, **tool_call}]}
                        if with_tool_call
                        else {"content": "answer"},
                        "finish_reason": "length",
                    }
                ],
            },
        ]
        content = "".join(f"data: {json.dumps(frame)}\n\n" for frame in frames) + "data: [DONE]\n\n"
        response = httpx.Response(
            200, headers={"Content-Type": "text/event-stream"}, content=content.encode()
        )
    else:
        message: dict[str, object] = {"content": "partial answer"}
        if with_tool_call:
            message["tool_calls"] = [tool_call]
        response = httpx.Response(
            200,
            json={"model": "model", "choices": [{"finish_reason": "length", "message": message}]},
        )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/models":
            return httpx.Response(404, json={})
        return response

    async def run() -> None:
        connection = ProviderConnection(
            "local", "lmstudio", ProcessingClass.LOCAL, endpoint="http://127.0.0.1:1234/v1"
        )
        profile = ModelProfile("reason", "local", "model", False, True)
        config = RuntimeConfiguration(
            {"local": connection}, {"reason": profile}, "reason", {"answer": "reason"}
        )
        app = RuntimeService(
            config,
            SelectionStore(tmp_path / "state"),
            provider_factory=lambda found_connection, found_profile: LMStudioAdapter(
                found_connection,
                found_profile,
                lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            ),
        )
        created = await app.create_session(
            "hello", [TOOL], output_schema=None if streaming else {"type": "object"}
        )
        settled = await app.wait(created["id"])
        assert settled["status"] == "failed"
        assert settled["failure"]["code"] == "provider_incomplete"
        assert settled["final_text"] is None
        assert settled["pending_tools"] == []
        events = app.events(created["id"])
        assert not any(event["type"] == "tool_requests" for event in events)
        assert events[-1]["type"] == "session_failed"
        record = app.sessions[created["id"]]
        assert [message.role for message in record.messages] == ["user"]
        if streaming:
            # Provisional deltas may exist; they are never promoted to a result.
            assert [event["type"] for event in events[:3]] == [
                "session_created",
                "provider_started",
                "assistant_text_delta",
            ]

    asyncio.run(run())
