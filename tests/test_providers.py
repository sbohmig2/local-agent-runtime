from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import httpx
import pytest

from local_agent_runtime.adapters.process import ProcessResult, run_process
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
    ModelProfile,
    ProcessingClass,
    ProviderConnection,
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
        return ProcessResult(0, json.dumps(payload), "")

    profile = ModelProfile("test", "provider", "exact-model", True, False)
    connection = ProviderConnection("provider", driver, ProcessingClass.EXTERNAL, command=command)
    adapter = adapter_type(connection, profile, runner, lambda _: "/trusted/" + command)
    result = asyncio.run(
        adapter.complete(Invocation((Message("user", "prompt-canary"),), (), Limits()))
    )
    assert result.text == "answer"
    assert result.effective_model is None  # Never fabricate provider confirmation.
    args, root = observed[0]
    assert not root.exists()
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
        CodexAdapter(connection, profile, runner=runner, which=lambda _: "/trusted/codex").health()
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
            connection, profile, runner=runner, which=lambda _: "/trusted/claude"
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
        GrokAdapter(connection, profile, runner=runner, which=lambda _: "/trusted/grok").health()
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
