"""Canonical versioned gateway schemas; shared by generation and conformance tests."""

from typing import Any

API_VERSION = "1.3.0"


def ref(name: str) -> dict[str, str]:
    return {"$ref": f"#/components/schemas/{name}"}


def array(items: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": items}


def obj(properties: dict[str, Any], optional: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": [key for key in properties if key not in optional],
        "additionalProperties": False,
    }


STRING = {"type": "string"}
BOOL = {"type": "boolean"}
NUMBER = {"type": "number"}
INTEGER = {"type": "integer"}
NULLABLE_STRING = {"type": ["string", "null"]}
JSON_OBJECT = {"type": "object", "additionalProperties": {}}
USAGE = {"type": "object", "additionalProperties": NUMBER}
PROCESSING = {"type": "string", "enum": ["local", "external"]}
REASONING_EFFORT = {
    "type": "string",
    "enum": ["minimal", "low", "medium", "high", "xhigh", "max"],
}
NULLABLE_REASONING_EFFORT = {"anyOf": [REASONING_EFFORT, {"type": "null"}]}

SCHEMAS: dict[str, dict[str, Any]] = {
    "ErrorDetail": obj({"code": STRING, "message": STRING}),
    "ErrorResponse": obj({"error": ref("ErrorDetail")}),
    "HealthResponse": obj(
        {
            "status": {"const": "available", "type": "string"},
            "package_version": STRING,
            "api_version": STRING,
        }
    ),
    "ProviderHealth": obj(
        {
            "status": {"type": "string", "enum": ["available", "unavailable", "inconclusive"]},
            "authenticated": {"type": ["boolean", "null"]},
            "installed": {"type": ["boolean", "null"]},
            "compatible": {"type": ["boolean", "null"]},
            "detail_code": NULLABLE_STRING,
            "effective_model": NULLABLE_STRING,
        }
    ),
    "Capabilities": obj(
        {
            name: BOOL
            for name in [
                "text_generation",
                "structured_output",
                "tool_requests",
                "token_streaming",
                "conversation_continuation",
                "model_discovery",
                "token_limit_control",
                "reasoning_effort_control",
                "provider_native_web",
            ]
        }
    ),
    "ReasoningOptions": obj(
        {"efforts": array(REASONING_EFFORT), "default": NULLABLE_REASONING_EFFORT}
    ),
    "ModelDiscovery": obj(
        {
            "supported": BOOL,
            "models": array(STRING),
            "detail_code": NULLABLE_STRING,
            "loaded_models": {"anyOf": [array(STRING), {"type": "null"}]},
            "details": array(ref("DiscoveredModel")),
        },
        ("loaded_models", "details"),
    ),
    "DiscoveredModel": obj(
        {
            "model": STRING,
            "display_name": STRING,
            "kind": {"type": "string", "enum": ["reasoning", "embedding", "unknown"]},
            "reasoning_efforts": array(REASONING_EFFORT),
            "default_reasoning_effort": NULLABLE_REASONING_EFFORT,
            "loaded": {"type": ["boolean", "null"]},
            "reasoning_efforts_known": BOOL,
        }
    ),
    "ModelOption": obj(
        {
            "id": STRING,
            "display_name": STRING,
            "reasoning": ref("ReasoningOptions"),
            "qualified_tasks": array(STRING),
            "loaded": {"type": ["boolean", "null"]},
        }
    ),
    "ModelOptionsResponse": obj(
        {
            "profile_id": STRING,
            "supported": BOOL,
            "checked_at": STRING,
            "detail_code": NULLABLE_STRING,
            "options": array(ref("ModelOption")),
        }
    ),
    "AdapterOption": obj(
        {
            "id": STRING,
            "profile_id": STRING,
            "model": STRING,
            "enabled": BOOL,
            "can_enable": BOOL,
            "can_disable": BOOL,
            "blocked_reason": NULLABLE_STRING,
        }
    ),
    "AdapterProbe": obj(
        {
            "state": {"type": "string", "enum": ["not_checked", "checked", "failed"]},
            "installed": {"type": ["boolean", "null"]},
            "detail_code": NULLABLE_STRING,
            "checked_at": NULLABLE_STRING,
        }
    ),
    "SupportedAdapter": obj(
        {
            "id": {
                "type": "string",
                "enum": ["codex_cli", "claude_cli", "grok_cli", "lmstudio", "openrouter"],
            },
            "label": STRING,
            "processing": PROCESSING,
            "supported": {"type": "boolean", "const": True},
            "configured": BOOL,
            "enabled": BOOL,
            "profile_ids": array(STRING),
            "options": array(ref("AdapterOption")),
            "probe": ref("AdapterProbe"),
            "discovery": ref("ModelDiscovery"),
        },
        ("discovery",),
    ),
    "AdaptersResponse": obj({"adapters": array(ref("SupportedAdapter"))}),
    "AdapterActivationRequest": obj({"option_id": STRING, "enabled": BOOL}),
    "ReasoningProfile": obj(
        {
            "id": STRING,
            "provider_id": STRING,
            "driver": STRING,
            "model": STRING,
            "configured": BOOL,
            "processing": PROCESSING,
            "allow_private_processing": BOOL,
            "qualified_tasks": array(STRING),
            "qualification": obj(
                {
                    "status": {"type": "string", "enum": ["qualified", "unqualified"]},
                    "tasks": array(STRING),
                }
            ),
            "selected": BOOL,
            "capabilities": ref("Capabilities"),
            "reasoning": ref("ReasoningOptions"),
            "health": ref("ProviderHealth"),
            "discovery": ref("ModelDiscovery"),
        },
        ("health", "discovery"),
    ),
    "ProfilesResponse": obj(
        {"selected_profile": STRING, "profiles": array(ref("ReasoningProfile"))}
    ),
    "SelectionRequest": obj({"profile_id": STRING}),
    "ToolDefinition": obj({"name": STRING, "description": STRING, "input_schema": JSON_OBJECT}),
    "ToolRequest": obj({"id": STRING, "name": STRING, "arguments": JSON_OBJECT}),
    "ToolResult": obj({"request_id": STRING, "name": STRING, "output": {}, "is_error": BOOL}),
    "ToolResultsRequest": obj({"results": array(ref("ToolResult"))}),
    "SessionRequest": obj(
        {
            "prompt": STRING,
            "instructions": STRING,
            "profile_id": STRING,
            "task_code": STRING,
            "private_processing": BOOL,
            "allow_external_processing": BOOL,
            "tools": array(ref("ToolDefinition")),
            "output_schema": JSON_OBJECT,
            "reasoning_effort": REASONING_EFFORT,
            "model_option_id": STRING,
        },
        (
            "instructions",
            "profile_id",
            "task_code",
            "private_processing",
            "allow_external_processing",
            "tools",
            "output_schema",
            "reasoning_effort",
            "model_option_id",
        ),
    ),
    "InputRequest": obj(
        {"prompt": STRING, "reasoning_effort": REASONING_EFFORT}, ("reasoning_effort",)
    ),
    "SessionResponse": obj(
        {
            "id": STRING,
            "profile_id": STRING,
            "provider_id": STRING,
            "adapter": STRING,
            "requested_model": STRING,
            "model_option_id": NULLABLE_STRING,
            "created_at": STRING,
            "updated_at": STRING,
            "finished_at": NULLABLE_STRING,
            "processing": PROCESSING,
            "task_code": NULLABLE_STRING,
            "status": {
                "type": "string",
                "enum": [
                    "created",
                    "running",
                    "waiting_for_tool",
                    "completed",
                    "failed",
                    "canceled",
                ],
            },
            "pending_tools": array(ref("ToolRequest")),
            "tool_rounds": INTEGER,
            "final_text": NULLABLE_STRING,
            "failure": {"anyOf": [ref("ErrorDetail"), {"type": "null"}]},
            "effective_model": NULLABLE_STRING,
            "effective_upstream": NULLABLE_STRING,
            "requested_reasoning_effort": NULLABLE_REASONING_EFFORT,
            "effective_reasoning_effort": NULLABLE_REASONING_EFFORT,
            "usage": USAGE,
            "limits": ref("ReasoningLimits"),
            "validation": {
                "type": "string",
                "enum": ["pending", "passed", "failed", "not_validated"],
            },
            "event_count": INTEGER,
        }
    ),
    "SessionEvent": obj(
        {"sequence": INTEGER, "type": STRING, "occurred_at": STRING, "payload": JSON_OBJECT}
    ),
    "EventsResponse": obj({"events": array(ref("SessionEvent"))}),
    "EmbeddingLimits": obj(
        {
            name: INTEGER
            for name in [
                "batch_size",
                "max_input_chars",
                "max_batch_chars",
                "timeout_seconds",
                "max_response_bytes",
            ]
        }
    ),
    "ReasoningLimits": obj(
        {
            name: INTEGER
            for name in [
                "timeout_seconds",
                "max_input_chars",
                "max_output_chars",
                "max_output_tokens",
                "max_tool_rounds",
            ]
        }
    ),
    "EmbeddingProfile": obj(
        {
            "id": STRING,
            "provider_id": STRING,
            "driver": STRING,
            "model": STRING,
            "dimensions": INTEGER,
            "processing": PROCESSING,
            "profile_fingerprint": STRING,
            "document_prefix": STRING,
            "query_prefix": STRING,
            "revision": STRING,
            "normalization": STRING,
            "distance_metric": STRING,
            "limits": ref("EmbeddingLimits"),
            "allow_private_processing": BOOL,
            "capabilities": obj({"text_embeddings": BOOL, "batching": BOOL, "streaming": BOOL}),
        }
    ),
    "EmbeddingProfilesResponse": obj({"profiles": array(ref("EmbeddingProfile"))}),
    "EmbeddingRequest": obj(
        {
            "profile_id": STRING,
            "inputs": array(STRING),
            "purpose": {"type": "string", "enum": ["document", "query"]},
            "expected_fingerprint": STRING,
            "allow_external_processing": BOOL,
            "private_processing": BOOL,
        },
        ("expected_fingerprint", "allow_external_processing", "private_processing"),
    ),
    "EmbeddingResponse": obj(
        {
            "profile_id": STRING,
            "provider_id": STRING,
            "adapter": STRING,
            "profile_fingerprint": STRING,
            "purpose": {"type": "string", "enum": ["document", "query"]},
            "requested_model": STRING,
            "effective_model": NULLABLE_STRING,
            "effective_upstream": NULLABLE_STRING,
            "processing": PROCESSING,
            "dimensions": INTEGER,
            "vectors": array(array(NUMBER)),
            "started_at": STRING,
            "finished_at": STRING,
            "usage": USAGE,
            "validation": {"type": "string", "const": "passed"},
        }
    ),
}

# operation id, method, path, request, response. This is also the client generation manifest.
OPERATIONS = (
    ("health", "get", "/v1/health", None, "HealthResponse"),
    ("profiles", "get", "/v1/profiles", None, "ProfilesResponse"),
    (
        "modelOptions",
        "get",
        "/v1/profiles/{profile_id}/model-options",
        None,
        "ModelOptionsResponse",
    ),
    ("adapters", "get", "/v1/adapters", None, "AdaptersResponse"),
    (
        "setAdapterActivation",
        "post",
        "/v1/adapter-activation",
        "AdapterActivationRequest",
        "AdaptersResponse",
    ),
    ("selectProfile", "post", "/v1/selection", "SelectionRequest", "ProfilesResponse"),
    ("createSession", "post", "/v1/sessions", "SessionRequest", "SessionResponse"),
    ("session", "get", "/v1/sessions/{session_id}", None, "SessionResponse"),
    ("events", "get", "/v1/sessions/{session_id}/events", None, "EventsResponse"),
    (
        "continueSession",
        "post",
        "/v1/sessions/{session_id}/input",
        "InputRequest",
        "SessionResponse",
    ),
    (
        "submitToolResults",
        "post",
        "/v1/sessions/{session_id}/tool-results",
        "ToolResultsRequest",
        "SessionResponse",
    ),
    ("cancelSession", "post", "/v1/sessions/{session_id}/cancel", None, "SessionResponse"),
    ("embeddingProfiles", "get", "/v1/embedding-profiles", None, "EmbeddingProfilesResponse"),
    ("embed", "post", "/v1/embeddings", "EmbeddingRequest", "EmbeddingResponse"),
)


def openapi() -> dict[str, Any]:
    paths: dict[str, Any] = {}
    for name, method, path, request, response in OPERATIONS:
        operation: dict[str, Any] = {
            "operationId": name,
            "responses": {
                "200": {
                    "description": "Success",
                    "content": {"application/json": {"schema": ref(response)}},
                },
                "default": {
                    "description": "Bounded failure",
                    "content": {"application/json": {"schema": ref("ErrorResponse")}},
                },
            },
        }
        parameters: list[dict[str, Any]] = []
        for parameter in ("session_id", "profile_id"):
            if "{" + parameter + "}" in path:
                parameters.append(
                    {"name": parameter, "in": "path", "required": True, "schema": STRING}
                )
        if name == "profiles":
            parameters.append({"name": "health", "in": "query", "schema": BOOL})
            parameters.append({"name": "discovery", "in": "query", "schema": BOOL})
        if name == "adapters":
            parameters.append({"name": "probe", "in": "query", "schema": BOOL})
        if name == "events":
            parameters.append(
                {"name": "after", "in": "query", "schema": {"type": "integer", "minimum": 0}}
            )
            operation["responses"]["200"]["content"]["text/event-stream"] = {"schema": STRING}
        if parameters:
            operation["parameters"] = parameters
        if request:
            operation["requestBody"] = {
                "required": True,
                "content": {"application/json": {"schema": ref(request)}},
            }
        paths.setdefault(path, {})[method] = operation
    return {
        "openapi": "3.1.0",
        "info": {"title": "Local Agent Runtime", "version": API_VERSION},
        "servers": [{"url": "http://127.0.0.1:8765"}],
        "security": [{"bearerAuth": []}],
        "paths": paths,
        "components": {
            "schemas": SCHEMAS,
            "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}},
        },
    }
