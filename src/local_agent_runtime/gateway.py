"""Authenticated loopback HTTP adapter for the runtime application service."""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from local_agent_runtime.api_contract import API_VERSION
from local_agent_runtime.contracts import SessionStatus, ToolDefinition, ToolResult
from local_agent_runtime.embeddings import EmbeddingPurpose
from local_agent_runtime.errors import RuntimeFailure, invalid_request
from local_agent_runtime.service import RuntimeService
from local_agent_runtime.version import PACKAGE_VERSION

TERMINAL_OR_PAUSED = {
    SessionStatus.WAITING_FOR_TOOL.value,
    SessionStatus.COMPLETED.value,
    SessionStatus.FAILED.value,
    SessionStatus.CANCELED.value,
}


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}},
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


async def _json_body(request: Request) -> Mapping[str, Any]:
    try:
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > 1_200_000:
                raise invalid_request("The request body exceeds its limit")
            data.extend(chunk)
        body = json.loads(data)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise invalid_request("The request body is invalid") from exc
    if not isinstance(body, Mapping):
        raise invalid_request("The request body must be an object")
    return body


def _tool_definitions(value: Any) -> tuple[ToolDefinition, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise invalid_request("The tool catalog is invalid")
    tools: list[ToolDefinition] = []
    for item in value:
        if (
            not isinstance(item, Mapping)
            or set(item) != {"name", "description", "input_schema"}
            or not isinstance(item["name"], str)
            or not isinstance(item["description"], str)
            or not isinstance(item["input_schema"], Mapping)
        ):
            raise invalid_request("A tool definition is invalid")
        tools.append(ToolDefinition(item["name"], item["description"], dict(item["input_schema"])))
    return tuple(tools)


def _tool_results(value: Any) -> tuple[ToolResult, ...]:
    if not isinstance(value, list):
        raise invalid_request("Tool results are invalid")
    results: list[ToolResult] = []
    for item in value:
        if (
            not isinstance(item, Mapping)
            or set(item) != {"request_id", "name", "output", "is_error"}
            or not isinstance(item["request_id"], str)
            or not isinstance(item["name"], str)
            or type(item["is_error"]) is not bool
        ):
            raise invalid_request("A tool result is invalid")
        results.append(
            ToolResult(
                item["request_id"],
                item["name"],
                item["output"],
                item["is_error"],
            )
        )
    return tuple(results)


def create_app(
    service: RuntimeService,
    *,
    bearer_token: str,
    allowed_origins: frozenset[str] = frozenset(),
    allowed_hosts: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1"}),
) -> Starlette:
    if not isinstance(bearer_token, str) or len(bearer_token) < 32:
        raise ValueError("Gateway bearer token must contain at least 32 characters")

    async def authorize(request: Request) -> Response | None:
        try:
            peer = ipaddress.ip_address(request.client.host) if request.client else None
        except ValueError:
            peer = None
        if peer is None or not peer.is_loopback:
            return _error(403, "non_loopback_refused", "The gateway accepts loopback clients only")
        try:
            host = urlsplit("//" + request.headers.get("host", "")).hostname
        except ValueError:
            host = None
        if host not in allowed_hosts:
            return _error(400, "invalid_host", "The request Host is not allowed")
        origin = request.headers.get("origin")
        if origin is not None and origin not in allowed_origins:
            return _error(403, "origin_refused", "The request Origin is not allowed")
        authorization = request.headers.get("authorization", "")
        expected = f"Bearer {bearer_token}"
        if not hmac.compare_digest(authorization.encode(), expected.encode()):
            return _error(401, "authentication_required", "Authentication is required")
        return None

    async def invoke(request: Request, operation: Any) -> Response:
        refused = await authorize(request)
        if refused:
            return refused
        try:
            return JSONResponse(await operation(), headers={"Cache-Control": "no-store"})
        except RuntimeFailure as exc:
            return _error(exc.status_code, exc.code, str(exc))
        except Exception:
            return _error(500, "internal_failure", "The runtime request failed safely")

    async def health(request: Request) -> Response:
        return await invoke(
            request,
            lambda: asyncio.sleep(
                0,
                result={
                    "status": "available",
                    "package_version": PACKAGE_VERSION,
                    "api_version": API_VERSION,
                },
            ),
        )

    async def profiles(request: Request) -> Response:
        health_value = request.query_params.get("health", "false")
        discovery_value = request.query_params.get("discovery", "false")
        if health_value not in {"true", "false"} or discovery_value not in {"true", "false"}:
            return _error(400, "invalid_request", "The profile catalog query is invalid")
        include_health = health_value == "true"
        include_discovery = discovery_value == "true"
        return await invoke(
            request,
            lambda: service.profile_state(
                include_health=include_health, include_discovery=include_discovery
            ),
        )

    async def select_profile(request: Request) -> Response:
        async def operation() -> dict[str, Any]:
            body = await _json_body(request)
            if set(body) != {"profile_id"}:
                raise invalid_request("The profile selection request is invalid")
            return await service.select_profile(body["profile_id"])

        return await invoke(request, operation)

    async def adapters(request: Request) -> Response:
        async def operation() -> dict[str, Any]:
            values = request.query_params.getlist("probe")
            if (
                set(request.query_params) - {"probe"}
                or len(values) > 1
                or (values and values[0] not in {"true", "false"})
            ):
                raise invalid_request("The adapter catalog query is invalid")
            return await service.adapter_state(probe=values == ["true"])

        return await invoke(request, operation)

    async def adapter_activation(request: Request) -> Response:
        async def operation() -> dict[str, Any]:
            body = await _json_body(request)
            if request.query_params or set(body) != {"option_id", "enabled"}:
                raise invalid_request("The adapter activation request is invalid")
            return await service.set_adapter_activation(body["option_id"], body["enabled"])

        return await invoke(request, operation)

    async def create_session(request: Request) -> Response:
        async def operation() -> dict[str, Any]:
            body = await _json_body(request)
            allowed = {
                "prompt",
                "instructions",
                "profile_id",
                "task_code",
                "private_processing",
                "tools",
                "allow_external_processing",
                "output_schema",
                "reasoning_effort",
            }
            if set(body) - allowed or "prompt" not in body:
                raise invalid_request("The session request is invalid")
            private = body.get("private_processing", False)
            external = body.get("allow_external_processing", False)
            if type(private) is not bool or type(external) is not bool:
                raise invalid_request("The private-processing flag is invalid")
            profile_id = body.get("profile_id")
            task_code = body.get("task_code")
            if profile_id is not None and not isinstance(profile_id, str):
                raise invalid_request("The session profile is invalid")
            if task_code is not None and not isinstance(task_code, str):
                raise invalid_request("The session task code is invalid")
            return await service.create_session(
                body["prompt"],
                _tool_definitions(body.get("tools")),
                instructions=body.get("instructions"),
                profile_id=profile_id,
                task_code=task_code,
                private_processing=private,
                allow_external_processing=external,
                output_schema=body.get("output_schema"),
                reasoning_effort=body.get("reasoning_effort"),
            )

        return await invoke(request, operation)

    async def get_session(request: Request) -> Response:
        return await invoke(
            request,
            lambda: asyncio.sleep(0, result=service.session(request.path_params["session_id"])),
        )

    async def session_input(request: Request) -> Response:
        async def operation() -> dict[str, Any]:
            body = await _json_body(request)
            if not {"prompt"} <= set(body) or set(body) - {"prompt", "reasoning_effort"}:
                raise invalid_request("The session input request is invalid")
            return await service.continue_session(
                request.path_params["session_id"],
                body["prompt"],
                body.get("reasoning_effort"),
            )

        return await invoke(request, operation)

    async def tool_results(request: Request) -> Response:
        async def operation() -> dict[str, Any]:
            body = await _json_body(request)
            if set(body) != {"results"}:
                raise invalid_request("The tool-result request is invalid")
            return await service.submit_tool_results(
                request.path_params["session_id"], _tool_results(body["results"])
            )

        return await invoke(request, operation)

    async def cancel(request: Request) -> Response:
        return await invoke(request, lambda: service.cancel(request.path_params["session_id"]))

    async def events(request: Request) -> Response:
        refused = await authorize(request)
        if refused:
            return refused
        try:
            after = int(request.query_params.get("after", "0"))
            initial_events = service.events(request.path_params["session_id"], after)
            if request.headers.get("accept") != "text/event-stream":
                return JSONResponse(
                    {"events": initial_events}, headers={"Cache-Control": "no-store"}
                )
        except (RuntimeFailure, ValueError) as exc:
            if isinstance(exc, RuntimeFailure):
                return _error(exc.status_code, exc.code, str(exc))
            return _error(400, "invalid_request", "The event cursor is invalid")

        async def stream() -> AsyncIterator[bytes]:
            cursor = after
            while True:
                batch = service.events(request.path_params["session_id"], cursor)
                for event in batch:
                    cursor = event["sequence"]
                    data = json.dumps(event, separators=(",", ":"))
                    yield f"id: {cursor}\nevent: {event['type']}\ndata: {data}\n\n".encode()
                state = service.session(request.path_params["session_id"])
                if state["status"] in TERMINAL_OR_PAUSED and not batch:
                    break
                if await request.is_disconnected():
                    break
                await asyncio.sleep(0.05)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    async def embedding_profiles(request: Request) -> Response:
        async def operation() -> dict[str, object]:
            return (
                service.embeddings.profile_state()
                if service.embeddings is not None
                else {"profiles": []}
            )

        return await invoke(request, operation)

    async def embed(request: Request) -> Response:
        async def operation() -> dict[str, object]:
            if service.embeddings is None:
                raise invalid_request("Embeddings are not configured")
            body = await _json_body(request)
            required = {"profile_id", "inputs", "purpose"}
            if not required <= set(body) or set(body) - required - {
                "expected_fingerprint",
                "allow_external_processing",
                "private_processing",
            }:
                raise invalid_request("The embedding request is invalid")
            try:
                purpose = EmbeddingPurpose(body["purpose"])
            except (ValueError, TypeError):
                raise invalid_request("Embedding purpose must be document or query") from None
            return await service.embeddings.embed(
                body["profile_id"],
                body["inputs"],
                purpose=purpose,
                expected_fingerprint=body.get("expected_fingerprint"),
                allow_external_processing=body.get("allow_external_processing", False),
                private_processing=body.get("private_processing", False),
            )

        return await invoke(request, operation)

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        yield
        await service.shutdown()

    return Starlette(
        debug=False,
        lifespan=lifespan,
        routes=[
            Route("/v1/embedding-profiles", embedding_profiles, methods=["GET"]),
            Route("/v1/embeddings", embed, methods=["POST"]),
            Route("/v1/health", health, methods=["GET"]),
            Route("/v1/profiles", profiles, methods=["GET"]),
            Route("/v1/adapters", adapters, methods=["GET"]),
            Route("/v1/adapter-activation", adapter_activation, methods=["POST"]),
            Route("/v1/selection", select_profile, methods=["POST"]),
            Route("/v1/sessions", create_session, methods=["POST"]),
            Route("/v1/sessions/{session_id}", get_session, methods=["GET"]),
            Route("/v1/sessions/{session_id}/events", events, methods=["GET"]),
            Route("/v1/sessions/{session_id}/input", session_input, methods=["POST"]),
            Route(
                "/v1/sessions/{session_id}/tool-results",
                tool_results,
                methods=["POST"],
            ),
            Route("/v1/sessions/{session_id}/cancel", cancel, methods=["POST"]),
        ],
    )
