"""Shared bounded CLI envelope codec; native provider wrappers live in adapters."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from local_agent_runtime.contracts import CompletionResult, Invocation, ModelProfile, ToolRequest
from local_agent_runtime.errors import RuntimeFailure, provider_unavailable


def _bounded_prompt(invocation: Invocation, *, allow_provider_native_web: bool = False) -> str:
    payload = {
        "messages": [message.public_dict() for message in invocation.messages],
        "tools": [tool.public_dict() for tool in invocation.tools],
        "final_content_json_schema": invocation.output_schema,
    }
    serialized = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    if len(serialized) > invocation.limits.max_input_chars:
        raise RuntimeFailure("input_limit_exceeded", "The session input exceeds its limit")
    names = ", ".join(tool.name for tool in invocation.tools) or "none"
    native_tool_policy = (
        "You may internally use only the provider's native public-web search and page-retrieval "
        "tools. Do not return those native calls in tool_calls. Do not use any other native tool. "
        if allow_provider_native_web
        else "Do not use any provider-native tool. "
    )
    return (
        "Return only one JSON object with keys content and tool_calls. content must be a "
        "string. tool_calls must be a list of objects with id, name, and arguments. "
        "arguments must be a JSON-encoded string containing the tool argument object. "
        "name must be exactly one of these supplied application-tool catalog names and nothing "
        f"else: {names}. {native_tool_policy}If no application tool is needed, return "
        "the final answer in content and an empty tool_calls list. If final_content_json_schema "
        "is supplied, content must be a JSON string conforming to that schema.\n\n" + serialized
    )


def _parse_envelope(raw: str, profile: ModelProfile) -> CompletionResult:
    value = raw.strip()
    if not value:
        raise provider_unavailable("The provider returned empty output")
    if len(value) > profile.limits.max_output_chars:
        raise RuntimeFailure("output_limit_exceeded", "The provider output exceeds its limit")
    try:
        payload: Any = json.loads(value)
    except (ValueError, RecursionError):
        raise provider_unavailable("The provider returned malformed structured output") from None
    if (
        not isinstance(payload, Mapping)
        or set(payload) != {"content", "tool_calls"}
        or not isinstance(payload["content"], str)
        or not isinstance(payload["tool_calls"], list)
        or len(payload["tool_calls"]) > 128
    ):
        raise provider_unavailable("The provider returned an invalid structured envelope")
    requests: list[ToolRequest] = []
    for raw_call in payload["tool_calls"]:
        if (
            not isinstance(raw_call, Mapping)
            or set(raw_call) != {"id", "name", "arguments"}
            or not isinstance(raw_call["id"], str)
            or not raw_call["id"]
            or len(raw_call["id"]) > 128
            or not isinstance(raw_call["name"], str)
            or not raw_call["name"]
            or not isinstance(raw_call["arguments"], str)
        ):
            raise provider_unavailable("The provider returned an invalid tool request")
        try:
            arguments = json.loads(raw_call["arguments"])
        except (ValueError, RecursionError):
            raise provider_unavailable("The provider returned invalid tool arguments") from None
        if not isinstance(arguments, dict):
            raise provider_unavailable("The provider returned invalid tool arguments")
        requests.append(ToolRequest(raw_call["id"], raw_call["name"], arguments))
    if not requests and not payload["content"].strip():
        raise provider_unavailable("The provider returned neither output nor a tool request")
    return CompletionResult(
        text=payload["content"].strip(),
        tool_requests=tuple(requests),
        effective_model=None,
    )


def _output_schema(names: tuple[str, ...] = ()) -> dict[str, Any]:
    """Constrain tool names to the exact catalog at the protocol level.

    A model that invents a name still fails closed in the application; naming the
    permitted values in the schema stops the common case at the provider instead.
    """
    name: dict[str, Any] = (
        {"type": "string", "enum": list(names)} if names else {"type": "string", "minLength": 1}
    )
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["content", "tool_calls"],
        "properties": {
            "content": {"type": "string"},
            "tool_calls": {
                "type": "array",
                "maxItems": 0 if not names else 128,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "name", "arguments"],
                    "properties": {
                        "id": {"type": "string", "minLength": 1},
                        "name": name,
                        "arguments": {"type": "string"},
                    },
                },
            },
        },
    }
