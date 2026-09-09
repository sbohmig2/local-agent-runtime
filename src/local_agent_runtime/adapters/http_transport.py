"""Bounded HTTP mechanics shared by adapters; no provider routing decisions."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import AsyncGenerator, Callable, Mapping
from typing import Any

import httpx

from local_agent_runtime.errors import RuntimeFailure


def default_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(trust_env=False, follow_redirects=False)


ClientFactory = Callable[[], httpx.AsyncClient]


def _response_failure(status_code: int) -> RuntimeFailure | None:
    if status_code in {401, 403}:
        return RuntimeFailure(
            "provider_authentication_failed",
            "Provider authentication failed",
            status_code=503,
        )
    if status_code == 429:
        return RuntimeFailure(
            "provider_rate_limited", "Provider rate limit reached", status_code=429
        )
    if not 200 <= status_code < 300:
        return RuntimeFailure(
            "provider_unavailable", "The provider request failed", status_code=503
        )
    return None


def safe_usage(value: object) -> dict[str, int | float]:
    if not isinstance(value, Mapping):
        return {}
    allowed = {
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "input_tokens",
        "output_tokens",
        "cost",
    }
    return {
        key: item
        for key, item in value.items()
        if key in allowed and type(item) in (int, float) and math.isfinite(item) and item >= 0
    }


def response_identity(value: object) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 256
        or any(ord(c) < 32 for c in value)
    ):
        raise RuntimeFailure(
            "invalid_provider_response", "Provider identity is invalid", status_code=502
        )
    return value


async def request_json(
    client_factory: ClientFactory,
    method: str,
    url: str,
    *,
    headers: Mapping[str, str],
    timeout: float,
    max_bytes: int,
    body: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        async with (
            asyncio.timeout(timeout),
            client_factory() as client,
            client.stream(
                method, url, headers=headers, json=body, timeout=timeout, follow_redirects=False
            ) as response,
        ):
            failure = _response_failure(response.status_code)
            if failure is not None:
                raise failure
            data = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=16_384):
                if len(data) + len(chunk) > max_bytes:
                    raise RuntimeFailure(
                        "output_limit_exceeded",
                        "Provider response exceeds its limit",
                        status_code=502,
                    )
                data.extend(chunk)
            result = json.loads(data)
            if not isinstance(result, dict):
                raise ValueError
            return result
    except RuntimeFailure:
        raise
    except (TimeoutError, httpx.TimeoutException):
        raise RuntimeFailure(
            "provider_timeout", "Provider request timed out", status_code=504
        ) from None
    except (httpx.HTTPError, ValueError, UnicodeError, RecursionError):
        raise RuntimeFailure(
            "invalid_provider_response",
            "Provider response is unavailable or invalid",
            status_code=502,
        ) from None


async def stream_json_sse(
    client_factory: ClientFactory,
    method: str,
    url: str,
    *,
    headers: Mapping[str, str],
    timeout: float,
    max_bytes: int,
    body: Mapping[str, Any],
) -> AsyncGenerator[dict[str, Any]]:
    """Decode a bounded provider SSE response without exposing raw frames."""

    try:
        async with (
            asyncio.timeout(timeout),
            client_factory() as client,
            client.stream(
                method, url, headers=headers, json=body, timeout=timeout, follow_redirects=False
            ) as response,
        ):
            failure = _response_failure(response.status_code)
            if failure is not None:
                raise failure
            buffered = bytearray()
            received = 0
            done = False
            async for chunk in response.aiter_bytes(chunk_size=16_384):
                received += len(chunk)
                if received > max_bytes:
                    raise RuntimeFailure(
                        "output_limit_exceeded",
                        "Provider response exceeds its limit",
                        status_code=502,
                    )
                buffered.extend(chunk)
                while True:
                    lf = buffered.find(b"\n\n")
                    crlf = buffered.find(b"\r\n\r\n")
                    boundaries = [
                        (index, size) for index, size in ((lf, 2), (crlf, 4)) if index >= 0
                    ]
                    if not boundaries:
                        break
                    boundary, marker_size = min(boundaries)
                    raw_frame = bytes(buffered[:boundary])
                    del buffered[: boundary + marker_size]
                    frame = raw_frame.replace(b"\r\n", b"\n").decode("utf-8")
                    data = "\n".join(
                        line[5:].removeprefix(" ")
                        for line in frame.split("\n")
                        if line.startswith("data:")
                    )
                    if not data:
                        continue
                    if data == "[DONE]":
                        done = True
                        break
                    value = json.loads(data)
                    if not isinstance(value, dict):
                        raise ValueError
                    yield value
                if done:
                    break
            if not done and buffered.strip():
                raise ValueError
    except RuntimeFailure:
        raise
    except (TimeoutError, httpx.TimeoutException):
        raise RuntimeFailure(
            "provider_timeout", "Provider request timed out", status_code=504
        ) from None
    except (httpx.HTTPError, ValueError, UnicodeError, RecursionError):
        raise RuntimeFailure(
            "invalid_provider_response",
            "Provider response is unavailable or invalid",
            status_code=502,
        ) from None
