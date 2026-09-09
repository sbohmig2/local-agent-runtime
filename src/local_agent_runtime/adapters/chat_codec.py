"""OpenAI-compatible chat encoding/decoding without provider selection."""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from local_agent_runtime.adapters.http_transport import response_identity, safe_usage
from local_agent_runtime.contracts import CompletionResult, Invocation, ModelProfile, ToolRequest
from local_agent_runtime.errors import RuntimeFailure


def chat_body(
    profile: ModelProfile, invocation: Invocation, *, stream: bool = False
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": profile.model,
        "messages": [],
        "max_tokens": invocation.limits.max_output_tokens,
        "stream": stream,
    }
    if stream:
        body["stream_options"] = {"include_usage": True}
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


@dataclass
class ChatStreamDecoder:
    """Accumulate OpenAI-compatible chunks and expose only assistant text."""

    invocation: Invocation
    text_parts: list[str] = field(default_factory=list)
    text_chars: int = 0
    calls: dict[int, dict[str, Any]] = field(default_factory=dict)
    finish_reason: str | None = None
    model: str | None = None
    usage: dict[str, int | float] = field(default_factory=dict)
    refused: bool = False

    def accept(self, payload: Mapping[str, Any]) -> tuple[str, ...]:
        failure = RuntimeFailure(
            "invalid_provider_response", "The provider returned invalid output", status_code=502
        )
        try:
            raw_model = response_identity(payload.get("model"))
            if raw_model is not None:
                if self.model is not None and self.model != raw_model:
                    raise failure
                self.model = raw_model
            self.usage.update(safe_usage(payload.get("usage")))
            choices = payload.get("choices")
            if not isinstance(choices, list):
                raise failure
            if not choices:
                return ()
            if len(choices) != 1 or self.finish_reason is not None:
                raise failure
            choice = choices[0]
            if not isinstance(choice, Mapping) or choice.get("index", 0) != 0:
                raise failure
            delta = choice.get("delta")
            if not isinstance(delta, Mapping):
                raise failure
            content = delta.get("content")
            if content is not None:
                if not isinstance(content, str):
                    raise failure
                if self.text_chars + len(content) > self.invocation.limits.max_output_chars:
                    raise RuntimeFailure(
                        "output_limit_exceeded", "The provider output exceeds its limit"
                    )
                if content:
                    self.text_parts.append(content)
                    self.text_chars += len(content)
            if delta.get("refusal"):
                self.refused = True
            raw_calls = delta.get("tool_calls", [])
            if not isinstance(raw_calls, list):
                raise failure
            for raw_call in raw_calls:
                if not isinstance(raw_call, Mapping):
                    raise failure
                index = raw_call.get("index")
                if type(index) is not int or not 0 <= index < 128:
                    raise failure
                call = self.calls.setdefault(
                    index, {"id": None, "type": "function", "name": "", "arguments": ""}
                )
                raw_id = raw_call.get("id")
                if raw_id is not None:
                    if not isinstance(raw_id, str) or (call["id"] not in {None, raw_id}):
                        raise failure
                    call["id"] = raw_id
                raw_type = raw_call.get("type")
                if raw_type is not None and raw_type != "function":
                    raise failure
                function = raw_call.get("function")
                if function is not None:
                    if not isinstance(function, Mapping):
                        raise failure
                    for key in ("name", "arguments"):
                        fragment = function.get(key)
                        if fragment is not None:
                            if not isinstance(fragment, str):
                                raise failure
                            call[key] += fragment
            finish = choice.get("finish_reason")
            if finish is not None:
                if not isinstance(finish, str):
                    raise failure
                self.finish_reason = finish
            return (content,) if content else ()
        except RuntimeFailure:
            raise
        except (TypeError, ValueError, AttributeError, RecursionError):
            raise failure from None

    def complete(self) -> CompletionResult:
        calls = [
            {
                "id": item["id"],
                "type": item["type"],
                "function": {"name": item["name"], "arguments": item["arguments"]},
            }
            for _, item in sorted(self.calls.items())
        ]
        return decode_chat(
            {
                "choices": [
                    {
                        "finish_reason": self.finish_reason,
                        "message": {
                            "content": "".join(self.text_parts),
                            "tool_calls": calls,
                            **({"refusal": True} if self.refused else {}),
                        },
                    }
                ],
                "model": self.model,
                "usage": self.usage,
            },
            self.invocation,
        )
