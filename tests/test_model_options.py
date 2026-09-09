from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import httpx
import pytest

from local_agent_runtime.adapters.process import ProcessResult
from local_agent_runtime.adapters.providers.claude import ClaudeAdapter
from local_agent_runtime.adapters.providers.codex import CodexAdapter
from local_agent_runtime.adapters.providers.grok import GrokAdapter
from local_agent_runtime.adapters.providers.lmstudio import LMStudioAdapter
from local_agent_runtime.adapters.selection import SelectionStore
from local_agent_runtime.contracts import (
    Capabilities,
    CompletionResult,
    DiscoveredModel,
    Invocation,
    ModelDiscovery,
    ModelKind,
    ModelProfile,
    ProcessingClass,
    ProviderConnection,
    ProviderHealth,
    ReasoningEffort,
    RuntimeConfiguration,
)
from local_agent_runtime.errors import RuntimeFailure
from local_agent_runtime.gateway import create_app
from local_agent_runtime.reasoning import ordered
from local_agent_runtime.service import RuntimeService


def cli_connection(driver: str, command: str) -> ProviderConnection:
    return ProviderConnection("provider", driver, ProcessingClass.EXTERNAL, command=command)


def test_codex_uses_structured_catalog_and_filters_provider_only_effort() -> None:
    async def rpc(*_args: object) -> ProcessResult:
        return ProcessResult(
            0,
            json.dumps(
                {
                    "id": 2,
                    "result": {
                        "data": [
                            {
                                "model": "gpt-5.6-sol",
                                "displayName": "GPT-5.6-Sol",
                                "hidden": False,
                                "supportedReasoningEfforts": [
                                    {"reasoningEffort": "low"},
                                    {"reasoningEffort": "high"},
                                    {"reasoningEffort": "ultra"},
                                ],
                                "defaultReasoningEffort": "low",
                            },
                            {
                                "model": "hidden",
                                "displayName": "Hidden",
                                "hidden": True,
                                "supportedReasoningEfforts": [],
                                "defaultReasoningEffort": None,
                            },
                        ],
                        "nextCursor": None,
                    },
                }
            ),
            "",
        )

    adapter = CodexAdapter(
        cli_connection("codex_cli", "codex"),
        ModelProfile("profile", "provider", "default", True, False),
        which=lambda _: "/bin/codex",
        resolve=lambda value: value,
        json_rpc_runner=rpc,
    )
    discovery = asyncio.run(adapter.discover_models())
    assert discovery.models == ("gpt-5.6-sol",)
    assert discovery.details[0].display_name == "GPT-5.6-Sol"
    assert discovery.details[0].reasoning_efforts == (
        ReasoningEffort.LOW,
        ReasoningEffort.HIGH,
    )
    assert discovery.details[0].default_reasoning_effort is ReasoningEffort.LOW
    assert discovery.details[0].reasoning_efforts_known is True


def test_codex_catalog_follows_bounded_structured_pagination() -> None:
    cursors: list[str | None] = []

    async def rpc(
        _arguments: Sequence[str],
        requests: Sequence[tuple[int, str]],
        _root: Path,
        _environment: Mapping[str, str],
        _timeout: float,
    ) -> ProcessResult:
        request_list = list(requests)
        params = json.loads(request_list[1][1])["params"]
        cursors.append(params.get("cursor"))
        index = len(cursors)
        return ProcessResult(
            0,
            json.dumps(
                {
                    "id": 2,
                    "result": {
                        "data": [
                            {
                                "model": f"model-{index}",
                                "displayName": f"Model {index}",
                                "hidden": False,
                                "supportedReasoningEfforts": [],
                                "defaultReasoningEffort": None,
                            }
                        ],
                        "nextCursor": "next" if index == 1 else None,
                    },
                }
            ),
            "",
        )

    adapter = CodexAdapter(
        cli_connection("codex_cli", "codex"),
        ModelProfile("profile", "provider", "default", True, False),
        which=lambda _: "/bin/codex",
        resolve=lambda value: value,
        json_rpc_runner=rpc,
    )
    assert asyncio.run(adapter.discover_models()).models == ("model-1", "model-2")
    assert cursors == [None, "next"]


def test_maintained_cli_catalogs_publish_exact_runtime_owned_identifiers() -> None:
    profile = ModelProfile("profile", "provider", "default", True, False)
    claude = ClaudeAdapter(cli_connection("claude_cli", "claude"), profile)
    grok = GrokAdapter(cli_connection("grok_cli", "grok"), profile)

    claude_catalog = asyncio.run(claude.discover_models())
    assert [(item.model, item.display_name) for item in claude_catalog.details] == [
        ("claude-fable-5-1", "Fable 5.1"),
        ("claude-opus-5", "Opus 5"),
        ("claude-sonnet-5", "Sonnet 5"),
        ("claude-haiku-4-5-20251001", "Haiku 4.5"),
    ]
    assert claude_catalog.details[-1].reasoning_efforts == ()
    assert all(item.reasoning_efforts_known for item in claude_catalog.details)
    grok_catalog = asyncio.run(grok.discover_models())
    assert [(item.model, item.display_name) for item in grok_catalog.details] == [
        ("grok-4.6", "Grok 4.6"),
        ("grok-4.5", "Grok 4.5"),
    ]


def test_catalog_qualification_issues_opaque_choices_without_consumer_model_lists(
    tmp_path: Path,
) -> None:
    connection = cli_connection("claude_cli", "claude")
    profile = ModelProfile(
        "claude-profile",
        "provider",
        "default",
        True,
        False,
        reasoning_efforts=(ReasoningEffort.LOW, ReasoningEffort.HIGH),
        default_reasoning_effort=ReasoningEffort.HIGH,
        catalog_model_tasks=("answer",),
    )
    service = RuntimeService(
        RuntimeConfiguration(
            {"provider": connection},
            {"claude-profile": profile},
            "claude-profile",
            {"answer": "claude-profile"},
        ),
        SelectionStore(tmp_path / "state"),
        provider_factory=lambda selected_connection, selected_profile: ClaudeAdapter(
            selected_connection, selected_profile
        ),
    )
    catalog = asyncio.run(service.model_options("claude-profile"))
    assert [item["display_name"] for item in catalog["options"]] == [
        "Fable 5.1",
        "Opus 5",
        "Sonnet 5",
        "Haiku 4.5",
    ]
    assert all(item["id"].startswith("model-") for item in catalog["options"])
    assert all("model" not in item for item in catalog["options"])
    assert catalog["options"][-1]["reasoning"] == {"efforts": [], "default": None}


def test_lm_studio_excludes_embedding_and_unknown_kind_models() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={"data": [{"id": "reasoner"}, {"id": "embedder"}, {"id": "mystery"}]},
            )
        return httpx.Response(
            200,
            json={
                "models": [
                    {"key": "reasoner", "type": "llm", "loaded_instances": []},
                    {"key": "embedder", "type": "embedding", "loaded_instances": []},
                ]
            },
        )

    connection = ProviderConnection(
        "provider",
        "lmstudio",
        ProcessingClass.LOCAL,
        endpoint="http://127.0.0.1:1234/v1",
    )
    adapter = LMStudioAdapter(
        connection,
        ModelProfile("profile", "provider", "reasoner", False, False),
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    discovery = asyncio.run(adapter.discover_models())
    assert discovery.models == ("embedder", "mystery", "reasoner")
    assert [item.model for item in discovery.details] == ["reasoner"]


class CatalogProvider:
    capabilities = Capabilities(model_discovery=True, reasoning_effort_control=True)

    def __init__(
        self,
        connection: ProviderConnection,
        profile: ModelProfile,
        observed: list[str],
        models: list[str],
    ) -> None:
        self.connection = connection
        self.profile = profile
        self.observed = observed
        self.models = models

    @property
    def reasoning_efforts(self) -> tuple[ReasoningEffort, ...]:
        return ordered(self.profile.reasoning_efforts)

    async def discover_models(self) -> ModelDiscovery:
        details = tuple(
            DiscoveredModel(
                model,
                model.upper(),
                ModelKind.REASONING,
                (ReasoningEffort.HIGH, ReasoningEffort.LOW),
                ReasoningEffort.LOW,
                reasoning_efforts_known=True,
            )
            for model in self.models
        )
        return ModelDiscovery(True, tuple(self.models), details=details)

    async def health(self) -> ProviderHealth:
        raise AssertionError

    async def complete(self, invocation: Invocation) -> CompletionResult:
        self.observed.append(self.profile.model)
        return CompletionResult(
            "answer",
            (),
            self.profile.model,
            effective_reasoning_effort=invocation.reasoning_effort,
        )


def option_runtime(tmp_path: Path) -> tuple[RuntimeService, list[str], list[str]]:
    connection = ProviderConnection(
        "local", "lmstudio", ProcessingClass.LOCAL, endpoint="http://127.0.0.1:1234/v1"
    )
    profile = ModelProfile(
        "profile",
        "local",
        "first",
        False,
        False,
        catalog_model_tasks=("answer",),
    )
    configuration = RuntimeConfiguration(
        {"local": connection}, {"profile": profile}, "profile", {"answer": "profile"}
    )
    observed: list[str] = []
    available = ["first", "second", "unqualified"]
    return (
        RuntimeService(
            configuration,
            SelectionStore(tmp_path / "state"),
            provider_factory=lambda selected_connection, selected: CatalogProvider(
                selected_connection, selected, observed, available
            ),
        ),
        observed,
        available,
    )


def test_second_runtime_issued_option_dispatches_exact_model_without_fallback(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        service, observed, available = option_runtime(tmp_path)
        catalog = await service.model_options("profile")
        option_ids = [item["id"] for item in catalog["options"]]
        assert len(option_ids) == 3
        assert all("model" not in item for item in catalog["options"])

        created = await service.create_session(
            "question",
            task_code="answer",
            model_option_id=option_ids[1],
            reasoning_effort="high",
        )
        settled = await service.wait(created["id"])
        assert settled["model_option_id"] == option_ids[1]
        assert settled["requested_model"] == "second"
        assert observed == ["second"]

        available.remove("second")
        with pytest.raises(RuntimeFailure) as failure:
            await service.create_session(
                "question", task_code="answer", model_option_id=option_ids[1]
            )
        assert failure.value.code == "model_option_unavailable"
        assert observed == ["second"]

    asyncio.run(run())


def test_gateway_exposes_options_and_accepts_only_the_opaque_option_id(tmp_path: Path) -> None:
    async def run() -> None:
        service, observed, _ = option_runtime(tmp_path)
        app = create_app(service, bearer_token="t" * 40)
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
        headers = {"Authorization": f"Bearer {'t' * 40}"}
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            unauthorized = await client.get("/v1/profiles/profile/model-options?extra=true")
            assert unauthorized.status_code == 401
            catalog = await client.get("/v1/profiles/profile/model-options", headers=headers)
            assert catalog.status_code == 200
            option_ids = [item["id"] for item in catalog.json()["options"]]
            assert len(option_ids) == 3
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "prompt": "question",
                    "task_code": "answer",
                    "model_option_id": option_ids[1],
                    "reasoning_effort": "low",
                },
            )
            assert created.status_code == 200
            await service.wait(created.json()["id"])
            assert observed == ["second"]

            refused = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "prompt": "question",
                    "task_code": "answer",
                    "model_option_id": "provider/raw-model",
                },
            )
            assert refused.status_code == 400
            assert refused.json()["error"]["code"] == "invalid_request"

    asyncio.run(run())


def test_disabled_profile_does_not_enumerate_models(tmp_path: Path) -> None:
    connection = cli_connection("claude_cli", "claude")
    profiles = {
        "active": ModelProfile("active", "provider", "default", True, False),
        "inactive": ModelProfile(
            "inactive",
            "provider",
            "default",
            True,
            False,
            catalog_model_tasks=("answer",),
        ),
    }
    service = RuntimeService(
        RuntimeConfiguration(
            {"provider": connection},
            profiles,
            "active",
            managed_profiles={"active": True, "inactive": False},
        ),
        SelectionStore(tmp_path / "state"),
        provider_factory=lambda selected_connection, selected_profile: ClaudeAdapter(
            selected_connection, selected_profile
        ),
    )

    with pytest.raises(RuntimeFailure) as failure:
        asyncio.run(service.model_options("inactive"))
    assert failure.value.status_code == 404
