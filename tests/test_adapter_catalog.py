from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from jsonschema import Draft202012Validator

import local_agent_runtime.service as service_module
from local_agent_runtime.adapters.activation import ActivationStore
from local_agent_runtime.adapters.catalog import AdapterCatalog
from local_agent_runtime.adapters.providers.lmstudio import LMStudioAdapter
from local_agent_runtime.adapters.selection import SelectionStore
from local_agent_runtime.api_contract import SCHEMAS
from local_agent_runtime.configuration import load_configuration
from local_agent_runtime.contracts import (
    ModelProfile,
    ProcessingClass,
    ProviderConnection,
    RuntimeConfiguration,
    SessionStatus,
)
from local_agent_runtime.errors import RuntimeFailure
from local_agent_runtime.gateway import create_app
from local_agent_runtime.providers import build_provider
from local_agent_runtime.service import RuntimeService, SessionRecord


def service(tmp_path: Path) -> RuntimeService:
    connection = ProviderConnection(
        "local", "lmstudio", ProcessingClass.LOCAL, endpoint="http://127.0.0.1:1234/v1"
    )
    config = RuntimeConfiguration(
        {"local": connection},
        {
            "primary": ModelProfile("primary", "local", "first", False, False),
            "dormant": ModelProfile("dormant", "local", "second", False, False),
        },
        "primary",
        managed_profiles={"primary": True, "dormant": False},
    )
    return RuntimeService(
        config,
        SelectionStore(tmp_path / "state"),
        provider_factory=build_provider,
        adapter_catalog=AdapterCatalog(),
        activation=ActivationStore(tmp_path / "state", "policy"),
    )


def validate_catalog(value: object) -> None:
    Draft202012Validator(
        {**SCHEMAS["AdaptersResponse"], "components": {"schemas": SCHEMAS}}
    ).validate(value)


def test_passive_catalog_exposes_every_adapter_without_probe(tmp_path: Path) -> None:
    runtime = service(tmp_path)

    class OfflineCatalog(AdapterCatalog):
        async def probe(self, *_: object) -> dict[str, Any]:
            pytest.fail("Passive inventory must not probe")

    runtime.adapter_catalog = OfflineCatalog()
    state = asyncio.run(runtime.adapter_state())
    validate_catalog(state)
    assert {row["id"] for row in state["adapters"]} == {
        "codex_cli",
        "claude_cli",
        "grok_cli",
        "lmstudio",
        "openrouter",
    }
    assert all(row["probe"]["state"] == "not_checked" for row in state["adapters"])
    local = next(row for row in state["adapters"] if row["id"] == "lmstudio")
    assert local["configured"] and local["enabled"]
    assert local["options"][1]["can_enable"]
    assert not next(row for row in state["adapters"] if row["id"] == "openrouter")["configured"]


def test_activation_persists_and_disabled_profiles_cannot_be_selected_or_invoked(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        runtime = service(tmp_path)
        assert [p["id"] for p in (await runtime.profile_state())["profiles"]] == ["primary"]
        with pytest.raises(RuntimeFailure):
            await runtime.select_profile("dormant")
        with pytest.raises(RuntimeFailure):
            await runtime.create_session("hello", profile_id="dormant")
        validate_catalog(await runtime.set_adapter_activation("dormant", True))
        assert len((await runtime.profile_state())["profiles"]) == 2
        # Enabling does not select a provider or alter retained sessions.
        assert runtime.selected_profile() == "primary"
        assert service(tmp_path)._enabled("dormant")
        await runtime.select_profile("dormant")
        with pytest.raises(RuntimeFailure, match="cannot be disabled") as failure:
            await runtime.set_adapter_activation("dormant", False)
        assert failure.value.code == "selected_profile"
        with pytest.raises(RuntimeFailure, match="default profile") as failure:
            await runtime.set_adapter_activation("primary", False)
        assert failure.value.code == "default_profile"
        state = await runtime.adapter_state()
        local = next(row for row in state["adapters"] if row["id"] == "lmstudio")
        assert local["options"][0]["blocked_reason"] == "default_profile"
        assert not local["options"][0]["can_disable"]
        restored = service(tmp_path)
        assert restored.selected_profile() == "dormant"
        assert restored._enabled("primary")
        await runtime.select_profile("primary")
        await runtime.set_adapter_activation("dormant", False)
        assert not service(tmp_path)._enabled("dormant")
        assert (tmp_path / "state/activation.json").stat().st_mode & 0o777 == 0o600

    asyncio.run(run())


@pytest.mark.parametrize("selection", ["removed-profile", "dormant", None])
def test_passive_catalog_survives_unresolvable_selection(
    tmp_path: Path, selection: str | None
) -> None:
    async def run() -> None:
        runtime = service(tmp_path)
        runtime.configuration = replace(
            runtime.configuration,
            profiles={
                **runtime.configuration.profiles,
                "other": ModelProfile("other", "local", "third", False, False),
            },
            managed_profiles={**runtime.configuration.managed_profiles, "other": True},
        )
        runtime.selection.write(selection or "primary")
        if selection is None:
            (tmp_path / "state/selection.json").write_text("invalid json")
        state = await runtime.adapter_state()
        validate_catalog(state)
        local = next(row for row in state["adapters"] if row["id"] == "lmstudio")
        assert local["options"][0]["blocked_reason"] == "default_profile"
        assert local["options"][1]["can_enable"]
        assert local["options"][2]["can_disable"]
        with pytest.raises(RuntimeFailure) as failure:
            await runtime.profile_state()
        assert failure.value.code == "selection_unavailable"
        with pytest.raises(RuntimeFailure) as failure:
            await runtime.create_session("hello")
        assert failure.value.code == "selection_unavailable"
        # A selected-profile repair remains explicit and restores normal reads.
        await runtime.select_profile("primary")
        assert (await runtime.profile_state())["selected_profile"] == "primary"

    asyncio.run(run())


def test_activation_overlay_cannot_disable_configured_default(tmp_path: Path) -> None:
    ActivationStore(tmp_path / "state", "policy").write({"primary": False})
    with pytest.raises(RuntimeFailure) as failure:
        service(tmp_path)
    assert failure.value.code == "activation_unavailable"


def test_disable_preserves_current_sessions_and_task_routes(tmp_path: Path) -> None:
    async def run() -> None:
        runtime = service(tmp_path)
        await runtime.set_adapter_activation("dormant", True)
        profile = runtime.configuration.profiles["dormant"]
        record = SessionRecord(
            "session", profile, runtime.configuration.providers["local"], (), [], False
        )
        record.status = SessionStatus.COMPLETED
        runtime.sessions[record.id] = record
        with pytest.raises(RuntimeFailure) as failure:
            await runtime.set_adapter_activation("dormant", False)
        assert failure.value.code == "profile_in_use"
        assert record.profile is profile
        runtime.sessions.clear()
        runtime.configuration = replace(runtime.configuration, task_routes={"answer": "dormant"})
        with pytest.raises(RuntimeFailure) as failure:
            await runtime.set_adapter_activation("dormant", False)
        assert failure.value.code == "task_route_in_use"

    asyncio.run(run())


def test_activation_overlay_rejects_policy_change_and_unsafe_files(tmp_path: Path) -> None:
    root = tmp_path / "state"
    store = ActivationStore(root, "first")
    store.write({"dormant": True})
    assert store.read() == {"dormant": True}
    with pytest.raises(RuntimeFailure):
        ActivationStore(root, "changed").read()
    store.path.chmod(0o644)
    with pytest.raises(RuntimeFailure):
        store.read()
    store.path.unlink()
    secret = tmp_path / "secret"
    secret.write_text("PRIVATE")
    store.path.symlink_to(secret)
    with pytest.raises(RuntimeFailure):
        store.write({"dormant": False})
    assert secret.read_text() == "PRIVATE"


def test_catalog_probes_are_explicit_bounded_and_failure_isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class SlowCatalog(AdapterCatalog):
        async def probe(self, driver: str, connections: object) -> dict[str, object]:
            if driver == "lmstudio":
                await asyncio.sleep(1)
            return {"installed": False, "detail_code": "executable_unavailable"}

    runtime = service(tmp_path)
    runtime.adapter_catalog = SlowCatalog()
    monkeypatch.setattr(service_module, "PROBE_TIMEOUT_SECONDS", 0.01)
    state = asyncio.run(runtime.adapter_state(probe=True))
    validate_catalog(state)
    assert [row["probe"]["state"] for row in state["adapters"]] == [
        "checked",
        "checked",
        "checked",
        "failed",
        "checked",
    ]


def test_detection_finds_only_executable_trusted_cli(tmp_path: Path) -> None:
    command = tmp_path / "claude"
    command.write_text("placeholder")
    catalog = AdapterCatalog(which=lambda value: str(command) if value == "claude" else None)
    assert asyncio.run(catalog.probe("claude_cli", []))["installed"] is False
    command.chmod(0o700)
    assert asyncio.run(catalog.probe("claude_cli", []))["installed"] is True
    assert asyncio.run(catalog.probe("grok_cli", []))["installed"] is False


@pytest.mark.parametrize(
    ("native", "expected"),
    [
        (None, None),
        ({"models": [{"key": "first", "loaded_instances": []}]}, ()),
        ({"models": [{"key": "first", "loaded_instances": [{"id": "first"}]}]}, ("first",)),
        ({"models": [{"key": "native-key", "loaded_instances": [{"id": "first"}]}]}, ("first",)),
        ({"models": [{"key": "first", "loaded_instances": [{"id": "custom"}]}]}, ("first",)),
        ({"models": [{"key": "other", "loaded_instances": [{"id": "custom"}]}]}, None),
        (
            {"models": [{"key": "other", "loaded_instances": [{"id": "first"}, {"id": "custom"}]}]},
            None,
        ),
    ],
)
def test_lmstudio_loaded_state_comes_only_from_native_api(
    native: dict[str, Any] | None, expected: tuple[str, ...] | None
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "first"}]})
        assert request.url.path == "/api/v1/models"
        return httpx.Response(404) if native is None else httpx.Response(200, json=native)

    connection = ProviderConnection(
        "local", "lmstudio", ProcessingClass.LOCAL, endpoint="http://127.0.0.1:1234/v1"
    )
    adapter = LMStudioAdapter(
        connection,
        ModelProfile("first", "local", "first", False, False),
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    result = asyncio.run(adapter.discover_models())
    assert result.models == ("first",)
    assert result.loaded_models == expected


def test_gateway_activation_accepts_only_issued_option_and_boolean(tmp_path: Path) -> None:
    async def run() -> None:
        app = create_app(service(tmp_path), bearer_token="t" * 40)
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            assert (await client.get("/v1/adapters")).status_code == 401
            client.headers["authorization"] = "Bearer " + "t" * 40
            response = await client.get("/v1/adapters")
            validate_catalog(response.json())
            for body in (
                {"option_id": "dormant", "enabled": True, "command": "/evil"},
                {"option_id": "new-profile", "enabled": True},
                {"option_id": "dormant", "enabled": "true"},
            ):
                assert (await client.post("/v1/adapter-activation", json=body)).status_code == 400
            assert (await client.get("/v1/adapters?probe=true&probe=false")).status_code == 400
            response = await client.post(
                "/v1/adapter-activation", json={"option_id": "dormant", "enabled": True}
            )
            assert response.status_code == 200
            validate_catalog(response.json())
            assert "credential" not in json.dumps(response.json())

    asyncio.run(run())


@pytest.mark.parametrize(
    "policy",
    [
        {"managed_profiles": {"absent": False}},
        {"managed_profiles": {"codex-default": "yes"}},
        {"command": "evil"},
    ],
)
def test_operator_activation_policy_rejects_unowned_fields(tmp_path: Path, policy: object) -> None:
    payload = yaml.safe_load(
        (Path(__file__).parents[1] / "config/runtime.example.yaml").read_text()
    )
    payload["activation_policy"] = policy
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(payload))
    with pytest.raises(RuntimeFailure):
        load_configuration(path)
