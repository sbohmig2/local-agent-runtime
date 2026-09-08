"""OpenAI-compatible chat encoding/decoding without provider selection."""

import json
from typing import Any

from local_agent_runtime.adapters.http_transport import response_identity, safe_usage
from local_agent_runtime.contracts import CompletionResult, Invocation, ModelProfile, ToolRequest
from local_agent_runtime.errors import RuntimeFailure


def chat_body(profile: ModelProfile, invocation: Invocation) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": profile.model,
        "messages": [],
        "max_tokens": invocation.limits.max_output_tokens,
        "stream": False,
    }
    for message in invocation.messages:
        item: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.tool_requests:
            item["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, allow_nan=False),
                    },
                }
                for call in message.tool_requests
            ]
        if message.tool_request_id is not None:
            item["tool_call_id"] = message.tool_request_id
            item["name"] = message.tool_name
        body["messages"].append(item)
    if invocation.tools:
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": dict(tool.input_schema),
                },
            }
            for tool in invocation.tools
        ]
        body["tool_choice"] = "auto"
    if invocation.reasoning_effort is not None:
        body["reasoning_effort"] = invocation.reasoning_effort.value
    if invocation.output_schema is not None:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "runtime_output",
                "strict": True,
                "schema": dict(invocation.output_schema),
            },
        }
    if (
        len(json.dumps(body, ensure_ascii=False, allow_nan=False))
        > invocation.limits.max_input_chars
    ):
        raise RuntimeFailure("input_limit_exceeded", "The session input exceeds its limit")
    return body


def decode_chat(payload: dict[str, Any], invocation: Invocation) -> CompletionResult:
    failure = RuntimeFailure(
        "invalid_provider_response", "The provider returned invalid output", status_code=502
    )
    try:
        choices = payload["choices"]
        if not isinstance(choices, list) or len(choices) != 1:
            raise failure
        choice = choices[0]
        if choice.get("finish_reason") not in {"stop", "tool_calls"}:
            raise RuntimeFailure(
                "provider_incomplete", "Provider output was refused or incomplete", status_code=502
            )
        message = choice["message"]
        if message.get("refusal"):
            raise RuntimeFailure(
                "provider_refused", "The provider refused the request", status_code=502
            )
        content = message.get("content") or ""
        if not isinstance(content, str) or len(content) > invocation.limits.max_output_chars:
            raise failure
        calls = message.get("tool_calls", [])
        if not isinstance(calls, list) or len(calls) > 128:
            raise failure
        requests = []
        for call in calls:
            if call.get("type") != "function":
                raise failure
            function = call["function"]
            args = json.loads(function["arguments"])
            if (
                not isinstance(args, dict)
                or not isinstance(call["id"], str)
                or not call["id"]
                or len(call["id"]) > 128
                or not isinstance(function["name"], str)
            ):
                raise failure
            requests.append(ToolRequest(call["id"], function["name"], args))
        if not content.strip() and not requests:
            raise failure
        return CompletionResult(
            content,
            tuple(requests),
            response_identity(payload.get("model")),
            response_identity(payload.get("provider")),
            safe_usage(payload.get("usage")),
        )
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError):
        raise failure from None
