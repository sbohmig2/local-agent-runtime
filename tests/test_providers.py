from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from local_agent_runtime.adapters.cli_base import CLIAdapterBase
from local_agent_runtime.adapters.process import ProcessResult, ProcessRunner, run_process
from local_agent_runtime.adapters.providers.claude import ClaudeAdapter
from local_agent_runtime.adapters.providers.codex import CodexAdapter
from local_agent_runtime.adapters.providers.grok import GrokAdapter
from local_agent_runtime.adapters.providers.lmstudio import LMStudioAdapter
from local_agent_runtime.adapters.providers.openrouter import OpenRouterAdapter
from local_agent_runtime.contracts import (
    HealthStatus,
    Invocation,
    Limits,
    Message,
    ModelDiscovery,
    ModelProfile,
    ProcessingClass,
    ProviderConnection,
    ReasoningEffort,
    ToolDefinition,
)
from local_agent_runtime.errors import RuntimeFailure


@pytest.mark.parametrize(
    "driver,adapter_type,command",
    [
        ("codex_cli", CodexAdapter, "codex"),
        ("claude_cli", ClaudeAdapter, "claude"),
        ("grok_cli", GrokAdapter, "grok"),
    ],
)
def test_cli_contract_and_isolation(
    driver: str,
    adapter_type: type[CodexAdapter] | type[ClaudeAdapter] | type[GrokAdapter],
    command: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("UNRELATED_SECRET", "secret-canary")
    provider_variables = {
        "codex_cli": {"CODEX_HOME", "CODEX_CA_CERTIFICATE"},
        "claude_cli": {"CLAUDE_CONFIG_DIR"},
        "grok_cli": {"XAI_API_KEY"},
    }
    for name in set().union(*provider_variables.values()):
        monkeypatch.setenv(name, f"{name.lower()}-canary")
    if driver == "grok_cli":
        auth = tmp_path / ".grok" / "auth.json"
        auth.parent.mkdir(mode=0o700)
        auth.write_text('{"test":"credential-canary"}')
        auth.chmod(0o600)
    observed: list[tuple[list[str], Path]] = []

    async def runner(
        args: Sequence[str],
        stdin: str | None,
        cwd: Path,
        environment: Mapping[str, str],
        timeout: float,
    ) -> ProcessResult:
        if "sessions" in args:
            assert driver == "grok_cli" and "delete" in args
            return ProcessResult(0, "", "")
        observed.append((list(args), cwd))
        assert "UNRELATED_SECRET" not in environment
        for owner, names in provider_variables.items():
            for name in names:
                assert (name in environment) is (owner == driver)
        assert "prompt-canary" not in " ".join(args)
        assert cwd.stat().st_mode & 0o077 == 0
        if driver == "grok_cli":
            assert environment["HOME"] == str(cwd)
            assert stdin is None
            assert "prompt-canary" in (cwd / "prompt.txt").read_text()
            assert (cwd / "prompt.txt").stat().st_mode & 0o077 == 0
            copied = cwd / ".grok" / "auth.json"
            assert copied.is_file()
            assert copied.stat().st_mode & 0o077 == 0
        else:
            assert stdin is not None and "prompt-canary" in stdin
        payload: object = {"content": "answer", "tool_calls": []}
        if driver == "claude_cli":
            payload = {"structured_output": payload, "is_error": False}
        if driver == "grok_cli":
            payload = {
                "text": json.dumps(payload),
                "stopReason": "end_turn",
                "structuredOutput": payload,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        return ProcessResult(0, json.dumps(payload), "")

    profile = ModelProfile("test", "provider", "exact-model", True, False)
    connection = ProviderConnection("provider", driver, ProcessingClass.EXTERNAL, command=command)
    adapter = adapter_type(
        connection,
        profile,
        runner,
        lambda _: "/trusted/" + command,
        lambda path: path + "-concrete",
    )
    result = asyncio.run(
        adapter.complete(Invocation((Message("user", "prompt-canary"),), (), Limits()))
    )
    assert result.text == "answer"
    assert result.effective_model is None  # Never fabricate provider confirmation.
    args, root = observed[0]
    assert not root.exists()
    # The installation binary is launched, never the launcher symlink spelling.
    assert args[0] == "/trusted/" + command + "-concrete"
    assert args[args.index("--model") + 1] == "exact-model"
    if driver == "codex_cli":
        assert "--ignore-user-config" in args
        assert any('":root"="deny"' in item for item in args)
    else:
        assert args[args.index("--tools") + 1] == ""
    if driver == "claude_cli":
        assert "--safe-mode" in args


@pytest.mark.parametrize("remote", [False, True])
def test_reasoning_http_contract(remote: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAR_TEST_KEY", "test-token")
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(
            200,
            json={
                "model": "model",
                "choices": [{"finish_reason": "stop", "message": {"content": "hello"}}],
                "usage": {"total_tokens": 4, "secret": 123},
            },
        )

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    connection = ProviderConnection(
        "provider",
        "openrouter" if remote else "lmstudio",
        ProcessingClass.EXTERNAL if remote else ProcessingClass.LOCAL,
        endpoint="https://openrouter.ai/api/v1" if remote else "http://127.0.0.1:1234/v1",
        credential_ref="env://LAR_TEST_KEY" if remote else None,
        upstream="openai" if remote else None,
    )
    profile = ModelProfile("profile", "provider", "model", remote, False)
    adapter = (
        OpenRouterAdapter(connection, profile, factory)
        if remote
        else LMStudioAdapter(connection, profile, factory)
    )
    result = asyncio.run(
        adapter.complete(Invocation((Message("user", "hi"),), (), Limits(), {"type": "object"}))
    )
    assert result.text == "hello"
    assert result.usage == {"total_tokens": 4}
    body = json.loads(observed[0].content)
    assert body["response_format"]["type"] == "json_schema"
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    if remote:
        assert body["provider"]["only"] == ["openai"]
        assert body["provider"]["allow_fallbacks"] is False


def test_process_output_is_bounded(tmp_path: Path) -> None:
    with pytest.raises(RuntimeFailure) as caught:
        asyncio.run(
            run_process(
                [sys.executable, "-c", "import sys; sys.stdout.write('x' * 2000000)"],
                None,
                tmp_path,
                {"PATH": os.environ["PATH"]},
                5,
            )
        )
    assert caught.value.code == "output_limit_exceeded"


def test_process_timeout_and_cancellation(tmp_path: Path) -> None:
    async def run() -> None:
        with pytest.raises(RuntimeFailure) as caught:
            await run_process(
                [sys.executable, "-c", "import time; time.sleep(30)"], None, tmp_path, {}, 0.05
            )
        assert caught.value.code == "provider_timeout"
        pid_file = tmp_path / "pid"
        code = (
            "import os,time,pathlib; pathlib.Path('pid').write_text(str(os.getpid())); "
            "time.sleep(30)"
        )
        task = asyncio.create_task(run_process([sys.executable, "-c", code], None, tmp_path, {}, 5))
        for _ in range(100):
            if pid_file.exists():
                break
            await asyncio.sleep(0.01)
        assert pid_file.exists()
        pid = int(pid_file.read_text())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)

    asyncio.run(run())


@pytest.mark.parametrize(
    "adapter_type,driver,command",
    [
        (CodexAdapter, "codex_cli", "codex"),
        (ClaudeAdapter, "claude_cli", "claude"),
        (GrokAdapter, "grok_cli", "grok"),
    ],
)
def test_missing_cli_is_distinct_from_authentication(
    adapter_type: type[CodexAdapter] | type[ClaudeAdapter] | type[GrokAdapter],
    driver: str,
    command: str,
) -> None:
    connection = ProviderConnection("provider", driver, ProcessingClass.EXTERNAL, command=command)
    profile = ModelProfile("profile", "provider", "default", True, False)
    health = asyncio.run(adapter_type(connection, profile, which=lambda _: None).health())
    assert health.status is HealthStatus.UNAVAILABLE
    assert health.installed is False
    assert health.authenticated is None
    assert health.compatible is None
    assert health.detail_code == "executable_unavailable"


def test_signed_out_cli_is_installed_but_unauthenticated(tmp_path: Path) -> None:
    async def runner(
        args: Sequence[str],
        _stdin: str | None,
        _cwd: Path,
        _environment: Mapping[str, str],
        _timeout: float,
    ) -> ProcessResult:
        return ProcessResult(0 if "--version" in args else 1, "", "")

    connection = ProviderConnection(
        "provider", "codex_cli", ProcessingClass.EXTERNAL, command="codex"
    )
    profile = ModelProfile("profile", "provider", "default", True, False)
    health = asyncio.run(
        CodexAdapter(
            connection,
            profile,
            runner=runner,
            which=lambda _: "/trusted/codex",
            resolve=lambda path: path,
        ).health()
    )
    assert health.status is HealthStatus.UNAVAILABLE
    assert health.installed is True
    assert health.authenticated is False
    assert health.compatible is None
    assert health.detail_code == "authentication_unavailable"


@pytest.mark.parametrize(
    "payload,expected_status,expected_authenticated,expected_detail",
    [
        ('{"loggedIn":true}', HealthStatus.AVAILABLE, True, "invocation_not_qualified"),
        ('{"loggedIn":false}', HealthStatus.UNAVAILABLE, False, "authentication_unavailable"),
        ("not-json", HealthStatus.INCONCLUSIVE, None, "authentication_inconclusive"),
    ],
)
def test_claude_health_distinguishes_authentication_states(
    payload: str,
    expected_status: HealthStatus,
    expected_authenticated: bool | None,
    expected_detail: str,
) -> None:
    async def runner(
        args: Sequence[str],
        _stdin: str | None,
        _cwd: Path,
        _environment: Mapping[str, str],
        _timeout: float,
    ) -> ProcessResult:
        return ProcessResult(0, "claude 1" if "--version" in args else payload, "")

    connection = ProviderConnection(
        "provider", "claude_cli", ProcessingClass.EXTERNAL, command="claude"
    )
    profile = ModelProfile("profile", "provider", "default", True, False)
    health = asyncio.run(
        ClaudeAdapter(
            connection,
            profile,
            runner=runner,
            which=lambda _: "/trusted/claude",
            resolve=lambda path: path,
        ).health()
    )
    assert health.status is expected_status
    assert health.installed is True
    assert health.authenticated is expected_authenticated
    assert health.compatible is None
    assert health.detail_code == expected_detail


def test_grok_health_is_explicitly_inconclusive_when_executable_is_present() -> None:
    async def runner(
        args: Sequence[str],
        _stdin: str | None,
        _cwd: Path,
        _environment: Mapping[str, str],
        _timeout: float,
    ) -> ProcessResult:
        assert "--version" in args
        return ProcessResult(0, "grok 1", "")

    connection = ProviderConnection(
        "provider", "grok_cli", ProcessingClass.EXTERNAL, command="grok"
    )
    profile = ModelProfile("profile", "provider", "default", True, False)
    health = asyncio.run(
        GrokAdapter(
            connection,
            profile,
            runner=runner,
            which=lambda _: "/trusted/grok",
            resolve=lambda path: path,
        ).health()
    )
    assert health.status is HealthStatus.INCONCLUSIVE
    assert health.installed is True
    assert health.authenticated is None
    assert health.compatible is None
    assert health.detail_code == "authentication_not_probed"


def test_lm_studio_health_distinguishes_model_mismatch() -> None:
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"data": [{"id": "other-model"}]})
            )
        )

    connection = ProviderConnection(
        "provider",
        "lmstudio",
        ProcessingClass.LOCAL,
        endpoint="http://127.0.0.1:1234/v1",
    )
    profile = ModelProfile("profile", "provider", "wanted-model", False, True)
    health = asyncio.run(LMStudioAdapter(connection, profile, factory).health())
    assert health.status is HealthStatus.INCONCLUSIVE
    assert health.installed is True
    assert health.compatible is False
    assert health.detail_code == "configured_model_not_in_catalog"


@pytest.mark.parametrize(
    "status_code,expected_status,expected_authenticated,expected_detail",
    [
        (200, HealthStatus.INCONCLUSIVE, True, "model_invocation_not_probed"),
        (401, HealthStatus.UNAVAILABLE, False, "provider_authentication_failed"),
    ],
)
def test_openrouter_health_distinguishes_authentication(
    status_code: int,
    expected_status: HealthStatus,
    expected_authenticated: bool,
    expected_detail: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LAR_TEST_KEY", "test-token")

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(status_code, json={}))
        )

    connection = ProviderConnection(
        "provider",
        "openrouter",
        ProcessingClass.EXTERNAL,
        endpoint="https://openrouter.ai/api/v1",
        credential_ref="env://LAR_TEST_KEY",
        upstream="openai",
    )
    profile = ModelProfile("profile", "provider", "model", True, False)
    health = asyncio.run(OpenRouterAdapter(connection, profile, factory).health())
    assert health.status is expected_status
    assert health.authenticated is expected_authenticated
    assert health.detail_code == expected_detail


def test_openrouter_credential_is_checked_before_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LAR_TEST_KEY", raising=False)
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    connection = ProviderConnection(
        "provider",
        "openrouter",
        ProcessingClass.EXTERNAL,
        endpoint="https://openrouter.ai/api/v1",
        credential_ref="env://LAR_TEST_KEY",
        upstream="openai",
    )
    adapter = OpenRouterAdapter(
        connection, ModelProfile("profile", "provider", "model", True, False), factory
    )
    with pytest.raises(RuntimeFailure) as caught:
        asyncio.run(adapter.complete(Invocation((Message("user", "secret-canary"),), (), Limits())))
    assert caught.value.code == "credential_unavailable"
    assert called is False
    assert "canary" not in str(caught.value)


@pytest.mark.parametrize(
    "payload,code",
    [
        (
            {"model": "wrong", "choices": [{"finish_reason": "stop", "message": {"content": "x"}}]},
            "provider_model_mismatch",
        ),
        (
            {
                "model": "model",
                "provider": "anthropic",
                "choices": [{"finish_reason": "stop", "message": {"content": "x"}}],
            },
            "provider_upstream_mismatch",
        ),
        ({"model": "model", "choices": []}, "invalid_provider_response"),
        (
            {
                "model": "model",
                "choices": [{"finish_reason": "length", "message": {"content": "x"}}],
            },
            "provider_incomplete",
        ),
    ],
)
def test_openrouter_identity_and_malformed_fail_explicitly(
    payload: dict[str, object], code: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAR_TEST_KEY", "test-token")

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
        )

    connection = ProviderConnection(
        "provider",
        "openrouter",
        ProcessingClass.EXTERNAL,
        endpoint="https://openrouter.ai/api/v1",
        credential_ref="env://LAR_TEST_KEY",
        upstream="openai",
    )
    adapter = OpenRouterAdapter(
        connection, ModelProfile("profile", "provider", "model", True, False), factory
    )
    with pytest.raises(RuntimeFailure) as caught:
        asyncio.run(adapter.complete(Invocation((Message("user", "hello"),), (), Limits())))
    assert caught.value.code == code


CLI_ADAPTERS: dict[str, tuple[type[CLIAdapterBase], str]] = {
    "codex_cli": (CodexAdapter, "codex"),
    "claude_cli": (ClaudeAdapter, "claude"),
    "grok_cli": (GrokAdapter, "grok"),
}


def cli_adapter(
    driver: str,
    runner: ProcessRunner,
    *,
    efforts: tuple[ReasoningEffort, ...] = (),
    default: ReasoningEffort | None = None,
    model: str = "exact-model",
) -> CLIAdapterBase:
    adapter_type, command = CLI_ADAPTERS[driver]
    profile = ModelProfile(
        "test",
        "provider",
        model,
        True,
        False,
        reasoning_efforts=efforts,
        default_reasoning_effort=default,
    )
    connection = ProviderConnection("provider", driver, ProcessingClass.EXTERNAL, command=command)
    return adapter_type(
        connection, profile, runner, lambda _: "/trusted/" + command, lambda path: path
    )


def native_payload(driver: str) -> str:
    payload: object = {"content": "answer", "tool_calls": []}
    if driver == "claude_cli":
        payload = {"structured_output": payload, "is_error": False}
    if driver == "grok_cli":
        payload = {"text": "ignored", "stopReason": "end_turn", "structuredOutput": payload}
    return json.dumps(payload)


@pytest.mark.parametrize(
    "driver,flag,expected",
    [
        ("codex_cli", "--config", 'model_reasoning_effort="high"'),
        ("claude_cli", "--effort", "high"),
        ("grok_cli", "--reasoning-effort", "high"),
    ],
)
def test_supported_effort_reaches_the_cli_and_is_reported(
    driver: str, flag: str, expected: str
) -> None:
    observed: list[list[str]] = []

    async def runner(
        args: Sequence[str],
        stdin: str | None,
        cwd: Path,
        environment: Mapping[str, str],
        timeout: float,
    ) -> ProcessResult:
        if "sessions" in args:
            return ProcessResult(0, "", "")
        observed.append(list(args))
        return ProcessResult(0, native_payload(driver), "")

    adapter = cli_adapter(driver, runner, efforts=(ReasoningEffort.HIGH,))
    result = asyncio.run(
        adapter.complete(
            Invocation((Message("user", "prompt"),), (), Limits(), None, ReasoningEffort.HIGH)
        )
    )
    # Forwarding an effort is a request; no CLI reports the level it applied.
    assert result.effective_reasoning_effort is None
    args = observed[0]
    assert expected in args[args.index(flag) + 1 :] or args[args.index(flag) + 1] == expected
    if driver == "codex_cli":
        # The trailing stdin marker must stay last after the effort override.
        assert args[-1] == "-"


@pytest.mark.parametrize("driver", sorted(CLI_ADAPTERS))
def test_unsupported_effort_never_starts_a_cli_process(driver: str) -> None:
    async def runner(*_args: object) -> ProcessResult:
        raise AssertionError("The provider must not be started")

    adapter = cli_adapter(driver, cast("ProcessRunner", runner), efforts=(ReasoningEffort.LOW,))
    assert adapter.reasoning_efforts == (ReasoningEffort.LOW,)
    with pytest.raises(RuntimeFailure) as failure:
        asyncio.run(
            adapter.complete(
                Invocation((Message("user", "prompt"),), (), Limits(), None, ReasoningEffort.MAX)
            )
        )
    assert failure.value.code == "reasoning_effort_unsupported"


@pytest.mark.parametrize(
    "driver,undeliverable",
    [
        # Each CLI's own level list, not the runtime vocabulary, bounds a profile.
        ("claude_cli", ReasoningEffort.MINIMAL),
        ("grok_cli", ReasoningEffort.MAX),
    ],
)
def test_a_profile_cannot_declare_a_level_its_route_cannot_send(
    driver: str, undeliverable: ReasoningEffort
) -> None:
    async def runner(*_args: object) -> ProcessResult:
        raise AssertionError("The provider must not be started")

    adapter = cli_adapter(driver, cast("ProcessRunner", runner), efforts=(undeliverable,))
    assert undeliverable not in adapter.TRANSPORT_EFFORTS
    with pytest.raises(RuntimeFailure) as failure:
        assert adapter.reasoning_efforts
    assert failure.value.code == "invalid_configuration"


@pytest.mark.parametrize("driver", sorted(CLI_ADAPTERS))
def test_an_unqualified_model_inherits_no_effort_options(driver: str) -> None:
    async def runner(*_args: object) -> ProcessResult:
        raise AssertionError("Discovery must not start a process")

    # A CLI accepting a level is not evidence that a given model supports it, and
    # an alias such as `default` names no exact model at all.
    for model in ("default", "some-unqualified-model"):
        adapter = cli_adapter(driver, cast("ProcessRunner", runner), model=model)
        assert adapter.VERIFIED_EFFORTS == {}
        assert adapter.reasoning_efforts == ()
        assert adapter.capabilities.reasoning_effort_control is False
        assert adapter.capabilities.model_discovery is True

    declared = cli_adapter(
        driver,
        cast("ProcessRunner", runner),
        model="a-model-the-operator-knows",
        efforts=(ReasoningEffort.HIGH,),
    )
    assert declared.reasoning_efforts == (ReasoningEffort.HIGH,)
    assert declared.capabilities.reasoning_effort_control is True


def test_grok_unwraps_its_native_session_envelope() -> None:
    payloads = [
        json.dumps({"text": '{"content":"from text","tool_calls":[]}', "stopReason": "end_turn"}),
        json.dumps(
            {
                "text": "unused",
                "stopReason": "end_turn",
                "structuredOutput": {"content": "structured", "tool_calls": []},
                "usage": {"input_tokens": 3},
            }
        ),
    ]
    expected = ["from text", "structured"]
    for raw, text in zip(payloads, expected, strict=True):

        async def runner(
            args: Sequence[str],
            stdin: str | None,
            cwd: Path,
            environment: Mapping[str, str],
            timeout: float,
            raw: str = raw,
        ) -> ProcessResult:
            return ProcessResult(0, "" if "sessions" in args else raw, "")

        adapter = cli_adapter("grok_cli", runner)
        result = asyncio.run(adapter.complete(Invocation((Message("user", "p"),), (), Limits())))
        assert result.text == text


@pytest.mark.parametrize(
    "raw,code",
    [
        ('{"content":"bare","tool_calls":[]}', "provider_unavailable"),
        ('{"text":"x","stopReason":"refusal"}', "provider_incomplete"),
        ('{"structuredOutput":[]}', "provider_unavailable"),
        ("not json", "provider_unavailable"),
    ],
)
def test_grok_refuses_malformed_or_incomplete_native_output(raw: str, code: str) -> None:
    async def runner(
        args: Sequence[str],
        stdin: str | None,
        cwd: Path,
        environment: Mapping[str, str],
        timeout: float,
    ) -> ProcessResult:
        return ProcessResult(0, "" if "sessions" in args else raw, "")

    adapter = cli_adapter("grok_cli", runner)
    with pytest.raises(RuntimeFailure) as failure:
        asyncio.run(adapter.complete(Invocation((Message("user", "p"),), (), Limits())))
    assert failure.value.code == code


def test_codex_declines_third_party_instruction_files_inside_its_sandbox() -> None:
    async def runner(
        args: Sequence[str],
        stdin: str | None,
        cwd: Path,
        environment: Mapping[str, str],
        timeout: float,
    ) -> ProcessResult:
        # Loading AGENTS.md re-executes Codex through its filesystem sandbox
        # helper, which the deny-root policy cannot permit; refusing the load
        # keeps the sandbox intact and drops an untrusted instruction source.
        assert "--config" in args and "project_doc_max_bytes=0" in args
        assert any('":root"="deny"' in item for item in args)
        assert not any("dangerously" in item for item in args)
        assert not any("--add-dir" in item or "disk-full-read-access" in item for item in args)
        return ProcessResult(0, native_payload("codex_cli"), "")

    adapter = cli_adapter("codex_cli", runner)
    assert (
        asyncio.run(adapter.complete(Invocation((Message("user", "p"),), (), Limits()))).text
        == "answer"
    )


def test_claude_receives_its_account_context_but_no_unrelated_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USER", "account-label")
    monkeypatch.setenv("UNRELATED_SECRET", "secret-canary")
    monkeypatch.setenv("XAI_API_KEY", "other-vendor-canary")

    async def runner(
        args: Sequence[str],
        stdin: str | None,
        cwd: Path,
        environment: Mapping[str, str],
        timeout: float,
    ) -> ProcessResult:
        # The CLI resolves its stored login against the account name; without it
        # an authenticated install reports itself as logged out.
        assert environment["USER"] == "account-label"
        assert "UNRELATED_SECRET" not in environment and "XAI_API_KEY" not in environment
        return ProcessResult(0, native_payload("claude_cli"), "")

    adapter = cli_adapter("claude_cli", runner)
    assert (
        asyncio.run(adapter.complete(Invocation((Message("user", "p"),), (), Limits()))).text
        == "answer"
    )


def test_lmstudio_discovers_models_and_only_claims_declared_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200, json={"data": [{"id": "local-model"}, {"id": "other"}, {"bad": 1}]}
            )
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": body.get("reasoning_effort", "absent")},
                    }
                ],
            },
        )

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    connection = ProviderConnection(
        "local", "lmstudio", ProcessingClass.LOCAL, endpoint="http://127.0.0.1:1234/v1"
    )
    silent = ModelProfile("silent", "local", "local-model", False, False)
    adapter = LMStudioAdapter(connection, silent, factory)
    # Per-model support is not observable from the endpoint, so nothing is claimed.
    assert adapter.VERIFIED_EFFORTS == {}
    assert adapter.reasoning_efforts == ()
    assert adapter.capabilities.reasoning_effort_control is False
    assert adapter.capabilities.model_discovery is True
    discovery = asyncio.run(adapter.discover_models())
    assert discovery == ModelDiscovery(True, ("local-model", "other"), None)
    with pytest.raises(RuntimeFailure) as failure:
        asyncio.run(
            adapter.complete(
                Invocation((Message("user", "p"),), (), Limits(), None, ReasoningEffort.LOW)
            )
        )
    assert failure.value.code == "reasoning_effort_unsupported"

    declared = ModelProfile(
        "declared",
        "local",
        "local-model",
        False,
        False,
        reasoning_efforts=(ReasoningEffort.LOW, ReasoningEffort.HIGH),
    )
    adapter = LMStudioAdapter(connection, declared, factory)
    assert adapter.reasoning_efforts == (ReasoningEffort.LOW, ReasoningEffort.HIGH)
    result = asyncio.run(
        adapter.complete(
            Invocation((Message("user", "p"),), (), Limits(), None, ReasoningEffort.HIGH)
        )
    )
    assert result.text == "high"
    # The endpoint does not report the applied level, so it is not claimed.
    assert result.effective_reasoning_effort is None

    beyond = ModelProfile(
        "beyond", "local", "local-model", False, False, reasoning_efforts=(ReasoningEffort.MAX,)
    )
    with pytest.raises(RuntimeFailure) as failure:
        assert LMStudioAdapter(connection, beyond, factory).reasoning_efforts
    assert failure.value.code == "invalid_configuration"


def test_openrouter_stays_out_of_reasoning_and_discovery_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LAR_TEST_KEY", "test-token")

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={}))
        )

    connection = ProviderConnection(
        "hosted",
        "openrouter",
        ProcessingClass.EXTERNAL,
        endpoint="https://openrouter.ai/api/v1",
        credential_ref="env://LAR_TEST_KEY",
        upstream="anthropic",
    )
    adapter = OpenRouterAdapter(
        connection, ModelProfile("hosted", "hosted", "a/model", True, False), factory
    )
    assert adapter.reasoning_efforts == ()
    assert adapter.capabilities.reasoning_effort_control is False
    assert asyncio.run(adapter.discover_models()).supported is False
    with pytest.raises(RuntimeFailure) as failure:
        asyncio.run(
            adapter.complete(
                Invocation((Message("user", "p"),), (), Limits(), None, ReasoningEffort.HIGH)
            )
        )
    assert failure.value.code == "reasoning_effort_unsupported"

    declared = ModelProfile(
        "declared",
        "hosted",
        "a/model",
        True,
        False,
        reasoning_efforts=(ReasoningEffort.LOW,),
    )
    with pytest.raises(RuntimeFailure) as failure:
        assert OpenRouterAdapter(connection, declared, factory).reasoning_efforts
    assert failure.value.code == "invalid_configuration"

    defaulted = ModelProfile(
        "defaulted",
        "hosted",
        "a/model",
        True,
        False,
        default_reasoning_effort=ReasoningEffort.LOW,
    )
    with pytest.raises(RuntimeFailure) as failure:
        assert OpenRouterAdapter(connection, defaulted, factory).reasoning_efforts
    assert failure.value.code == "invalid_configuration"


@pytest.mark.parametrize(
    "payload,code",
    [
        ({"text": "x", "stopReason": "end_turn", "is_error": True}, "provider_failure"),
        ({"text": "x", "stopReason": "end_turn", "isError": True}, "provider_failure"),
        ({"text": "x", "stopReason": "end_turn", "error": "refused"}, "provider_failure"),
        # A parsable payload does not make an early stop a completion.
        (
            {
                "stopReason": "max_turns",
                "structuredOutput": {"content": "partial", "tool_calls": []},
            },
            "provider_incomplete",
        ),
        (
            {
                "stopReason": "length",
                "structuredOutput": {"content": "partial", "tool_calls": []},
            },
            "provider_incomplete",
        ),
        (
            {
                "stopReason": "interrupted",
                "structuredOutput": {"content": "partial", "tool_calls": []},
            },
            "provider_incomplete",
        ),
        (
            {"stopReason": 7, "structuredOutput": {"content": "x", "tool_calls": []}},
            "provider_incomplete",
        ),
    ],
)
def test_grok_refuses_failed_or_early_stopped_turns(payload: dict[str, object], code: str) -> None:
    async def runner(
        args: Sequence[str],
        stdin: str | None,
        cwd: Path,
        environment: Mapping[str, str],
        timeout: float,
    ) -> ProcessResult:
        return ProcessResult(0, "" if "sessions" in args else json.dumps(payload), "")

    adapter = cli_adapter("grok_cli", runner)
    with pytest.raises(RuntimeFailure) as failure:
        asyncio.run(adapter.complete(Invocation((Message("user", "p"),), (), Limits())))
    assert failure.value.code == code


@pytest.mark.parametrize(
    "model_usage,expected",
    [
        ({"grok-4.6-build": {"inputTokens": 1}}, "grok-4.6-build"),
        # Helper models make the primary identity ambiguous, so nothing is claimed.
        ({"grok-4.6-build": {}, "grok-helper": {}}, None),
        ({}, None),
        ("not-a-mapping", None),
    ],
)
def test_grok_only_reports_an_unambiguous_effective_model(
    model_usage: object, expected: str | None
) -> None:
    payload = {
        "stopReason": "end_turn",
        "structuredOutput": {"content": "answer", "tool_calls": []},
        "usage": {"input_tokens": 3, "output_tokens": 2},
        "modelUsage": model_usage,
    }

    async def runner(
        args: Sequence[str],
        stdin: str | None,
        cwd: Path,
        environment: Mapping[str, str],
        timeout: float,
    ) -> ProcessResult:
        return ProcessResult(0, "" if "sessions" in args else json.dumps(payload), "")

    result = asyncio.run(
        cli_adapter("grok_cli", runner).complete(Invocation((Message("user", "p"),), (), Limits()))
    )
    assert result.effective_model == expected
    assert result.usage == {"input_tokens": 3, "output_tokens": 2}


@pytest.mark.parametrize("driver", sorted(CLI_ADAPTERS))
def test_an_oversized_native_wrapper_is_refused_before_it_is_parsed(
    driver: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    parsed: list[str] = []
    payload = {
        "content": "answer",
        "tool_calls": [],
        "structured_output": {"content": "answer", "tool_calls": []},
        "structuredOutput": {"content": "answer", "tool_calls": []},
        "stopReason": "end_turn",
        # Wrapper-only fields never reach the envelope, so they must be bounded here.
        "thought": "t" * 20_000,
    }

    async def runner(
        args: Sequence[str],
        stdin: str | None,
        cwd: Path,
        environment: Mapping[str, str],
        timeout: float,
    ) -> ProcessResult:
        return ProcessResult(0, "" if "sessions" in args else json.dumps(payload), "")

    adapter = cli_adapter(driver, runner)
    monkeypatch.setattr(type(adapter), "decode", lambda self, raw: parsed.append(raw), raising=True)
    limits = Limits(max_output_chars=1_000)
    with pytest.raises(RuntimeFailure) as failure:
        asyncio.run(adapter.complete(Invocation((Message("user", "p"),), (), limits)))
    assert failure.value.code == "output_limit_exceeded"
    assert parsed == []


def test_the_envelope_schema_names_the_exact_catalog_tools() -> None:
    schemas: list[dict[str, Any]] = []
    prompts: list[str] = []

    async def runner(
        args: Sequence[str],
        stdin: str | None,
        cwd: Path,
        environment: Mapping[str, str],
        timeout: float,
    ) -> ProcessResult:
        if "sessions" in args:
            return ProcessResult(0, "", "")
        schemas.append(json.loads((cwd / "output-schema.json").read_text()))
        prompts.append(stdin or (cwd / "prompt.txt").read_text())
        return ProcessResult(0, native_payload("codex_cli"), "")

    def tool_calls(schema: dict[str, Any]) -> dict[str, Any]:
        return cast("dict[str, Any]", schema["properties"]["tool_calls"])

    tool = ToolDefinition("vault_balance", "Read a balance", {"type": "object", "properties": {}})
    adapter = cli_adapter("codex_cli", runner)
    asyncio.run(adapter.complete(Invocation((Message("user", "p"),), (tool,), Limits())))
    assert tool_calls(schemas[0])["items"]["properties"]["name"] == {
        "type": "string",
        "enum": ["vault_balance"],
    }
    assert "vault_balance" in prompts[0]

    # With no catalog the model may not request a tool at all.
    asyncio.run(adapter.complete(Invocation((Message("user", "p"),), (), Limits())))
    assert tool_calls(schemas[1])["maxItems"] == 0


@pytest.mark.parametrize("driver", sorted(CLI_ADAPTERS))
def test_a_launcher_symlink_is_resolved_to_its_installation_binary(
    driver: str, tmp_path: Path
) -> None:
    """A packaged CLI re-executes itself, and a sandbox refuses the symlink spelling."""
    adapter_type, command = CLI_ADAPTERS[driver]
    installation = tmp_path / "releases" / "current"
    installation.mkdir(parents=True)
    concrete = installation / f"{command}.bin"
    concrete.write_text("#!/bin/sh\n")
    concrete.chmod(0o700)
    launcher = tmp_path / "bin" / command
    launcher.parent.mkdir()
    launcher.symlink_to(concrete)
    launched: list[str] = []

    async def runner(
        args: Sequence[str],
        stdin: str | None,
        cwd: Path,
        environment: Mapping[str, str],
        timeout: float,
    ) -> ProcessResult:
        if "sessions" in args:
            return ProcessResult(0, "", "")
        launched.append(args[0])
        return ProcessResult(0, native_payload(driver), "")

    profile = ModelProfile("test", "provider", "exact-model", True, False)
    connection = ProviderConnection("provider", driver, ProcessingClass.EXTERNAL, command=command)
    adapter = adapter_type(connection, profile, runner, lambda _: str(launcher))
    asyncio.run(adapter.complete(Invocation((Message("user", "p"),), (), Limits())))
    assert launched == [str(concrete.resolve())]

    # Health must describe the same installation the completion path launches.
    asyncio.run(adapter.health())
    assert launched[1] == str(concrete.resolve())


@pytest.mark.parametrize("driver", sorted(CLI_ADAPTERS))
def test_an_unresolvable_or_unexecutable_target_stays_unavailable(
    driver: str, tmp_path: Path
) -> None:
    adapter_type, command = CLI_ADAPTERS[driver]

    async def runner(*_args: object) -> ProcessResult:
        raise AssertionError("An unresolved executable must not be started")

    dangling = tmp_path / "dangling"
    dangling.symlink_to(tmp_path / "absent")
    unexecutable = tmp_path / "plain"
    unexecutable.write_text("not executable")
    unexecutable.chmod(0o600)
    directory = tmp_path / "directory"
    directory.mkdir()

    profile = ModelProfile("test", "provider", "exact-model", True, False)
    connection = ProviderConnection("provider", driver, ProcessingClass.EXTERNAL, command=command)
    for target in (dangling, unexecutable, directory, tmp_path / "missing"):

        def located(_command: str, at: str = str(target)) -> str:
            return at

        adapter = adapter_type(connection, profile, cast("ProcessRunner", runner), located)
        assert adapter.executable() is None
        health = asyncio.run(adapter.health())
        assert health.installed is False
        assert health.detail_code == "executable_unavailable"
        with pytest.raises(RuntimeFailure) as failure:
            asyncio.run(adapter.complete(Invocation((Message("user", "p"),), (), Limits())))
        assert failure.value.code == "provider_unavailable"
