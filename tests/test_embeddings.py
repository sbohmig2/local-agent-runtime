from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any

import httpx
import pytest

from local_agent_runtime.adapters.embedding_codec import decode_embeddings
from local_agent_runtime.adapters.providers.lmstudio_embeddings import LMStudioEmbeddingAdapter
from local_agent_runtime.adapters.providers.openrouter_embeddings import OpenRouterEmbeddingAdapter
from local_agent_runtime.contracts import ProcessingClass, ProviderConnection
from local_agent_runtime.embeddings import (
    EmbeddingLimits,
    EmbeddingProfile,
    EmbeddingPurpose,
    EmbeddingResult,
    EmbeddingService,
)
from local_agent_runtime.errors import RuntimeFailure
from local_agent_runtime.providers import build_embedding_provider

LOCAL = ProviderConnection(
    "local", "lmstudio", ProcessingClass.LOCAL, endpoint="http://127.0.0.1:1234/v1"
)
REMOTE = ProviderConnection(
    "router",
    "openrouter",
    ProcessingClass.EXTERNAL,
    endpoint="https://openrouter.ai/api/v1",
    credential_ref="env://LAR_TEST_KEY",
    upstream="openai",
)
PROFILE = EmbeddingProfile(
    "vectors", "local", "embed-exact", 2, document_prefix="doc: ", query_prefix="query: "
)


class FakeEmbeddingProvider:
    def __init__(self) -> None:
        self.inputs: list[tuple[str, ...]] = []
        self.result = EmbeddingResult(((3.0, 4.0),), "embed-exact")

    async def embed(self, inputs: tuple[str, ...]) -> EmbeddingResult:
        self.inputs.append(inputs)
        return self.result


def service(
    profile: EmbeddingProfile = PROFILE, connection: ProviderConnection = LOCAL
) -> tuple[EmbeddingService, FakeEmbeddingProvider]:
    fake = FakeEmbeddingProvider()
    return EmbeddingService(
        {connection.id: connection}, {profile.id: profile}, provider_factory=lambda *_: fake
    ), fake


@pytest.mark.parametrize(
    "purpose,prefix", [(EmbeddingPurpose.DOCUMENT, "doc: "), (EmbeddingPurpose.QUERY, "query: ")]
)
def test_prefix_fingerprint_and_provenance(purpose: EmbeddingPurpose, prefix: str) -> None:
    app, fake = service()
    result = asyncio.run(
        app.embed(
            "vectors", ["hello"], purpose=purpose, expected_fingerprint=PROFILE.fingerprint(LOCAL)
        )
    )
    assert fake.inputs == [(prefix + "hello",)]
    assert result["vectors"] == [[3.0, 4.0]]
    assert result["profile_fingerprint"] == PROFILE.fingerprint(LOCAL)
    assert result["requested_model"] == result["effective_model"] == "embed-exact"
    assert result["validation"] == "passed"
    assert "hello" not in json.dumps({k: v for k, v in result.items() if k != "vectors"})


@pytest.mark.parametrize(
    "change",
    [
        {"model": "new"},
        {"dimensions": 3},
        {"revision": "2"},
        {"document_prefix": "new"},
        {"query_prefix": "new"},
        {"normalization": "l2"},
        {"distance_metric": "dot"},
    ],
)
def test_vector_space_changes_invalidate_fingerprint(change: dict[str, Any]) -> None:
    assert replace(PROFILE, **change).fingerprint(LOCAL) != PROFILE.fingerprint(LOCAL)


def test_runtime_limits_and_display_names_do_not_change_vector_space() -> None:
    profile = replace(PROFILE, id="renamed", limits=EmbeddingLimits(batch_size=1))
    assert profile.fingerprint(LOCAL) == PROFILE.fingerprint(LOCAL)


def test_mismatch_fails_before_provider_is_called() -> None:
    app, fake = service()
    with pytest.raises(RuntimeFailure) as caught:
        asyncio.run(
            app.embed(
                "vectors", ["secret"], purpose=EmbeddingPurpose.QUERY, expected_fingerprint="stale"
            )
        )
    assert caught.value.code == "embedding_profile_mismatch"
    assert fake.inputs == []
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("inputs", [[], [""], [" "], [True], "text", ["x" * 8_001], ["x"] * 25])
def test_invalid_inputs_fail_without_call(inputs: Any) -> None:
    app, fake = service()
    with pytest.raises(RuntimeFailure):
        asyncio.run(app.embed("vectors", inputs, purpose=EmbeddingPurpose.DOCUMENT))
    assert fake.inputs == []


def test_prefix_and_batch_total_are_included_in_limits() -> None:
    app, fake = service(
        replace(PROFILE, limits=EmbeddingLimits(max_input_chars=8, max_batch_chars=10))
    )
    with pytest.raises(RuntimeFailure):
        asyncio.run(app.embed("vectors", ["1234", "1234"], purpose=EmbeddingPurpose.DOCUMENT))
    assert fake.inputs == []


def test_external_consent_and_private_policy() -> None:
    profile = replace(PROFILE, provider_id="router", allow_external_processing=True)
    app, fake = service(profile, REMOTE)
    with pytest.raises(RuntimeFailure, match="not allowed"):
        asyncio.run(app.embed("vectors", ["secret"], purpose=EmbeddingPurpose.QUERY))
    with pytest.raises(RuntimeFailure, match="not allowed"):
        asyncio.run(
            app.embed(
                "vectors",
                ["secret"],
                purpose=EmbeddingPurpose.QUERY,
                allow_external_processing=True,
                private_processing=True,
            )
        )
    assert fake.inputs == []
    asyncio.run(
        app.embed(
            "vectors", ["public"], purpose=EmbeddingPurpose.QUERY, allow_external_processing=True
        )
    )
    assert len(fake.inputs) == 1


@pytest.mark.parametrize(
    "vector",
    [[0, 0], [True, 2], [1, float("nan")], [1, float("inf")], [1], [1, "2"], [10**1000, 1]],
)
def test_invalid_vectors(vector: list[Any]) -> None:
    with pytest.raises(RuntimeFailure):
        decode_embeddings({"data": [{"index": 0, "embedding": vector}]}, 1, 2)


@pytest.mark.parametrize("indices", [[0, 0], [1, 2], [-1, 0], [True, 0]])
def test_duplicate_missing_or_invalid_indices(indices: list[Any]) -> None:
    with pytest.raises(RuntimeFailure):
        decode_embeddings(
            {"data": [{"index": index, "embedding": [1, 2]} for index in indices]}, 2, 2
        )


def test_reorders_response_and_normalizes_if_requested() -> None:
    decoded = decode_embeddings(
        {
            "model": "embed-exact",
            "data": [{"index": 1, "embedding": [1, 2]}, {"index": 0, "embedding": [3, 4]}],
        },
        2,
        2,
    )
    assert decoded.vectors == ((3, 4), (1, 2))
    app, _ = service(replace(PROFILE, normalization="l2"))
    result = asyncio.run(app.embed("vectors", ["text"], purpose=EmbeddingPurpose.QUERY))
    assert result["vectors"] == [[0.6, 0.8]]


def test_effective_model_mismatch_and_unknown_identity() -> None:
    app, fake = service()
    fake.result = replace(fake.result, effective_model="different")
    with pytest.raises(RuntimeFailure) as caught:
        asyncio.run(app.embed("vectors", ["text"], purpose=EmbeddingPurpose.QUERY))
    assert caught.value.code == "embedding_model_mismatch"
    fake.result = replace(fake.result, effective_model=None)
    result = asyncio.run(app.embed("vectors", ["text"], purpose=EmbeddingPurpose.QUERY))
    assert result["effective_model"] is None


@pytest.mark.parametrize("remote", [False, True])
def test_real_adapter_wire_contract(remote: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAR_TEST_KEY", "test-token-not-real")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200, json={"model": "embed-exact", "data": [{"index": 0, "embedding": [3, 4]}]}
        )

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)

    if remote:
        adapter: OpenRouterEmbeddingAdapter | LMStudioEmbeddingAdapter
        adapter = OpenRouterEmbeddingAdapter(
            REMOTE, replace(PROFILE, provider_id="router"), factory
        )
    else:
        adapter = LMStudioEmbeddingAdapter(LOCAL, PROFILE, factory)
    result = asyncio.run(adapter.embed(("doc: hello",)))
    assert result.vectors == ((3, 4),)
    body = json.loads(requests[0].content)
    assert body["model"] == "embed-exact"
    assert body["input"] == ["doc: hello"]
    assert requests[0].url.path.endswith("/v1/embeddings")
    if remote:
        assert body["provider"]["allow_fallbacks"] is False
        assert body["provider"]["only"] == ["openai"]
    else:
        assert "authorization" not in requests[0].headers


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "provider_authentication_failed"),
        (429, "provider_rate_limited"),
        (302, "provider_unavailable"),
    ],
)
def test_http_failures_are_safe(status: int, code: str) -> None:
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(status, text="secret-canary"))
        )

    adapter = LMStudioEmbeddingAdapter(LOCAL, PROFILE, factory)
    with pytest.raises(RuntimeFailure) as caught:
        asyncio.run(adapter.embed(("private-canary",)))
    assert caught.value.code == code
    assert "canary" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_oversized_response_and_absent_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 2_000))
        )

    adapter = LMStudioEmbeddingAdapter(
        LOCAL, replace(PROFILE, limits=EmbeddingLimits(max_response_bytes=1_024)), factory
    )
    with pytest.raises(RuntimeFailure) as caught:
        asyncio.run(adapter.embed(("hello",)))
    assert caught.value.code == "output_limit_exceeded"
    monkeypatch.delenv("LAR_TEST_KEY", raising=False)
    remote = OpenRouterEmbeddingAdapter(REMOTE, replace(PROFILE, provider_id="router"), factory)
    with pytest.raises(RuntimeFailure) as caught:
        asyncio.run(remote.embed(("hello",)))
    assert caught.value.code == "credential_unavailable"


@pytest.mark.parametrize("driver", ["codex_cli", "claude_cli", "grok_cli", "unknown"])
def test_cli_routes_never_fall_back_to_embeddings(driver: str) -> None:
    with pytest.raises(RuntimeFailure):
        build_embedding_provider(replace(LOCAL, driver=driver), PROFILE)


def test_cancellation_releases_capacity() -> None:
    async def run() -> None:
        entered = asyncio.Event()

        class Slow:
            async def embed(self, inputs: tuple[str, ...]) -> EmbeddingResult:
                entered.set()
                await asyncio.Event().wait()
                raise AssertionError

        app = EmbeddingService(
            {"local": LOCAL},
            {"vectors": PROFILE},
            provider_factory=lambda *_: Slow(),
            max_concurrency=1,
        )
        task = asyncio.create_task(app.embed("vectors", ["hello"], purpose=EmbeddingPurpose.QUERY))
        await entered.wait()
        with pytest.raises(RuntimeFailure) as caught:
            await app.embed("vectors", ["hello"], purpose=EmbeddingPurpose.QUERY)
        assert caught.value.code == "capacity_exceeded"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert app._active == 0

    asyncio.run(run())


def test_embedding_timeout_releases_capacity() -> None:
    async def run() -> None:
        class Slow:
            async def embed(self, inputs: tuple[str, ...]) -> EmbeddingResult:
                await asyncio.sleep(30)
                raise AssertionError

        profile = replace(PROFILE, limits=EmbeddingLimits(timeout_seconds=1))
        app = EmbeddingService(
            {"local": LOCAL},
            {"vectors": profile},
            provider_factory=lambda *_: Slow(),
            max_concurrency=1,
        )
        with pytest.raises(RuntimeFailure) as caught:
            await app.embed("vectors", ["hello"], purpose=EmbeddingPurpose.QUERY)
        assert caught.value.code == "provider_timeout"
        assert app._active == 0

    asyncio.run(run())


def test_openrouter_embedding_upstream_change_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LAR_TEST_KEY", "test-token-not-real")

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={
                        "model": "embed-exact",
                        "provider": "anthropic",
                        "data": [{"index": 0, "embedding": [3, 4]}],
                    },
                )
            )
        )

    adapter = OpenRouterEmbeddingAdapter(REMOTE, replace(PROFILE, provider_id="router"), factory)
    with pytest.raises(RuntimeFailure) as caught:
        asyncio.run(adapter.embed(("hello",)))
    assert caught.value.code == "provider_upstream_mismatch"
