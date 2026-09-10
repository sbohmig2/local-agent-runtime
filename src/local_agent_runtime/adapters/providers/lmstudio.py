"""LM Studio reasoning adapter and local model-catalog semantics."""

import json
from collections.abc import Mapping
from contextlib import aclosing
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any, ClassVar

from local_agent_runtime.adapters.chat_codec import ChatStreamDecoder, chat_body, decode_chat
from local_agent_runtime.adapters.http_transport import (
    ClientFactory,
    default_client,
    request_json,
    stream_json_sse,
)
from local_agent_runtime.configuration import validate_connection
from local_agent_runtime.contracts import (
    Capabilities,
    CompletionResult,
    DiscoveredModel,
    HealthStatus,
    Invocation,
    ModelDiscovery,
    ModelKind,
    ModelProfile,
    ProviderConnection,
    ProviderHealth,
    ReasoningEffort,
)
from local_agent_runtime.errors import RuntimeFailure
from local_agent_runtime.ports import TextDeltaSink
from local_agent_runtime.reasoning import resolve_efforts

# LM Studio may send one JSON/SSE frame per generated token and repeats the
# model identity in each frame. The per-token allowance covers its observed
# OpenAI envelope plus a bounded text/reasoning fragment; visible text also gets
# the worst JSON escape expansion independently. Extra frames cover role,
# terminal, usage, and stream sentinels that do not represent output tokens.
SSE_FIXED_BYTES = 64_000
SSE_JSON_BYTES_PER_TEXT_CHAR = 6
SSE_FRAME_METADATA_BYTES = 512
SSE_TOKEN_PAYLOAD_BYTES = 256
SSE_EXTRA_FRAMES = 8
UNSUPPORTED_GRAMMAR_SCHEMA_KEYWORDS = frozenset({"minLength", "maxLength"})
SCHEMA_MAP_KEYWORDS = frozenset(
    {
        "$defs",
        "definitions",
        "dependencies",
        "dependentSchemas",
        "patternProperties",
        "properties",
    }
)
SCHEMA_SINGLE_KEYWORDS = frozenset(
    {
        "additionalItems",
        "additionalProperties",
        "contains",
        "contentSchema",
        "else",
        "if",
        "items",
        "not",
        "propertyNames",
        "then",
        "unevaluatedItems",
        "unevaluatedProperties",
    }
)
SCHEMA_LIST_KEYWORDS = frozenset({"allOf", "anyOf", "oneOf", "prefixItems"})
SCHEMA_SEQUENCE_KEYWORDS = SCHEMA_SINGLE_KEYWORDS | SCHEMA_LIST_KEYWORDS


def _stream_response_limit(profile: ModelProfile, invocation: Invocation) -> int:
    """Bound native chunks, including repeated identity and non-display token frames."""

    model_identity_bytes = len(json.dumps(profile.model, ensure_ascii=True).encode("utf-8"))
    frame_bytes = SSE_FRAME_METADATA_BYTES + SSE_TOKEN_PAYLOAD_BYTES + model_identity_bytes
    return (
        SSE_FIXED_BYTES
        + invocation.limits.max_output_chars * SSE_JSON_BYTES_PER_TEXT_CHAR
        + (invocation.limits.max_output_tokens + SSE_EXTRA_FRAMES) * frame_bytes
    )


def _grammar_compatible_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Copy a schema without mistaking property or literal names for keywords."""

    compatible: dict[str, Any] = {}
    for key, value in schema.items():
        if key in UNSUPPORTED_GRAMMAR_SCHEMA_KEYWORDS:
            continue
        if key in SCHEMA_MAP_KEYWORDS and isinstance(value, Mapping):
            compatible[key] = {
                name: _grammar_compatible_schema(item)
                if isinstance(item, Mapping)
                else deepcopy(item)
                for name, item in value.items()
            }
        elif key in SCHEMA_SINGLE_KEYWORDS and isinstance(value, Mapping):
            compatible[key] = _grammar_compatible_schema(value)
        elif key in SCHEMA_SEQUENCE_KEYWORDS and isinstance(value, list):
            compatible[key] = [
                _grammar_compatible_schema(item) if isinstance(item, Mapping) else deepcopy(item)
                for item in value
            ]
        else:
            compatible[key] = deepcopy(value)
    return compatible


def _lmstudio_chat_body(
    profile: ModelProfile, invocation: Invocation, *, stream: bool = False
) -> dict[str, Any]:
    """Translate only the provider wire schema; callers retain the original contract."""

    wire_invocation = replace(
        invocation,
        tools=tuple(
            replace(tool, input_schema=_grammar_compatible_schema(tool.input_schema))
            for tool in invocation.tools
        ),
        output_schema=(
            _grammar_compatible_schema(invocation.output_schema)
            if invocation.output_schema is not None
            else None
        ),
    )
    return chat_body(profile, wire_invocation, stream=stream)


@dataclass
class LMStudioAdapter:
    connection: ProviderConnection
    profile: ModelProfile
    client_factory: ClientFactory = default_client

    #: `reasoning_effort` is an OpenAI-compatible request field the endpoint accepts.
    TRANSPORT_EFFORTS: ClassVar[tuple[ReasoningEffort, ...]] = (
        ReasoningEffort.MINIMAL,
        ReasoningEffort.LOW,
        ReasoningEffort.MEDIUM,
        ReasoningEffort.HIGH,
    )
    #: A loaded local model may ignore the field entirely and the endpoint reports
    #: no per-model support, so no model is pre-qualified here.
    VERIFIED_EFFORTS: ClassVar[Mapping[str, tuple[ReasoningEffort, ...]]] = {}

    def __post_init__(self) -> None:
        validate_connection(self.connection)

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(
            token_streaming=True,
            token_limit_control=True,
            model_discovery=True,
            reasoning_effort_control=bool(self.reasoning_efforts),
        )

    @property
    def reasoning_efforts(self) -> tuple[ReasoningEffort, ...]:
        return resolve_efforts(self.profile, self.TRANSPORT_EFFORTS, self.VERIFIED_EFFORTS)

    async def _catalog(self) -> list[str]:
        payload = await request_json(
            self.client_factory,
            "GET",
            f"{self.connection.endpoint}/models",
            headers={},
            timeout=15,
            max_bytes=200_000,
        )
        catalog = payload.get("data")
        if not isinstance(catalog, list):
            raise RuntimeFailure(
                "invalid_provider_response",
                "The provider returned an invalid catalog",
                status_code=502,
            )
        return sorted(
            {
                item["id"]
                for item in catalog
                if isinstance(item, dict)
                and isinstance(item.get("id"), str)
                and 0 < len(item["id"]) <= 256
            }
        )[:512]

    async def discover_models(self) -> ModelDiscovery:
        try:
            models = tuple(await self._catalog())
        except RuntimeFailure as exc:
            return ModelDiscovery(supported=True, detail_code=exc.code)
        loaded: tuple[str, ...] | None = None
        details: tuple[DiscoveredModel, ...] = ()
        detail_code: str | None = None
        try:
            payload = await request_json(
                self.client_factory,
                "GET",
                f"{(self.connection.endpoint or '').removesuffix('/v1')}/api/v1/models",
                headers={},
                timeout=5,
                max_bytes=200_000,
            )
            native = payload.get("models")
            if (
                isinstance(native, list)
                and len(native) <= 512
                and all(
                    isinstance(item, dict)
                    and isinstance(item.get("key"), str)
                    and isinstance(item.get("loaded_instances"), list)
                    and all(
                        isinstance(instance, dict) and isinstance(instance.get("id"), str)
                        for instance in item["loaded_instances"]
                    )
                    for item in native
                )
            ):
                # Match either the model key or a custom instance identifier to
                # the compatible catalog; downloaded presence never implies loaded.
                identities = {
                    identity
                    for item in native
                    if item["loaded_instances"]
                    for identity in [
                        item["key"],
                        *[instance["id"] for instance in item["loaded_instances"]],
                    ]
                }
                unresolved = any(
                    item["key"] not in models and instance["id"] not in models
                    for item in native
                    for instance in item["loaded_instances"]
                )
                if not unresolved:
                    loaded = tuple(model for model in models if model in identities)
                    matches: dict[str, Mapping[str, object]] = {}
                    ambiguous = False
                    for item in native:
                        identities_for_item = {
                            item["key"],
                            *[instance["id"] for instance in item["loaded_instances"]],
                        }
                        for model in models:
                            if model in identities_for_item:
                                if model in matches:
                                    ambiguous = True
                                matches[model] = item
                    if not ambiguous and all(
                        item.get("type") in {"llm", "embedding"} for item in native
                    ):
                        details = tuple(
                            DiscoveredModel(
                                model,
                                model,
                                ModelKind.REASONING,
                                loaded=model in loaded,
                            )
                            for model in models
                            if model in matches and matches[model]["type"] == "llm"
                        )
                        detail_code = None
        except RuntimeFailure:
            # Older servers expose only /v1/models: loaded state stays unknown.
            pass
        return ModelDiscovery(
            supported=True,
            models=models,
            detail_code=detail_code,
            loaded_models=loaded,
            details=details,
        )

    async def health(self) -> ProviderHealth:
        try:
            matched = self.profile.model in await self._catalog()
            return ProviderHealth(
                HealthStatus.AVAILABLE if matched else HealthStatus.INCONCLUSIVE,
                installed=True,
                authenticated=None,
                compatible=matched,
                detail_code=(
                    "protocol_not_qualified" if matched else "configured_model_not_in_catalog"
                ),
            )
        except RuntimeFailure as exc:
            return ProviderHealth(
                HealthStatus.UNAVAILABLE,
                installed=False,
                authenticated=None,
                compatible=None,
                detail_code=exc.code,
            )

    async def complete(self, invocation: Invocation) -> CompletionResult:
        if (
            invocation.reasoning_effort is not None
            and invocation.reasoning_effort not in self.reasoning_efforts
        ):
            raise RuntimeFailure(
                "reasoning_effort_unsupported",
                "The requested reasoning effort is not supported by this profile",
            )
        payload = await request_json(
            self.client_factory,
            "POST",
            f"{self.connection.endpoint}/chat/completions",
            headers={"Content-Type": "application/json"},
            timeout=invocation.limits.timeout_seconds,
            max_bytes=max(64_000, invocation.limits.max_output_chars * 6),
            body=_lmstudio_chat_body(self.profile, invocation),
        )
        return self._checked_result(decode_chat(payload, invocation))

    async def complete_streaming(
        self, invocation: Invocation, emit_text: TextDeltaSink
    ) -> CompletionResult:
        if (
            invocation.reasoning_effort is not None
            and invocation.reasoning_effort not in self.reasoning_efforts
        ):
            raise RuntimeFailure(
                "reasoning_effort_unsupported",
                "The requested reasoning effort is not supported by this profile",
            )
        body = _lmstudio_chat_body(self.profile, invocation, stream=True)
        decoder = ChatStreamDecoder(invocation)
        async with aclosing(
            stream_json_sse(
                self.client_factory,
                "POST",
                f"{self.connection.endpoint}/chat/completions",
                headers={"Accept": "text/event-stream", "Content-Type": "application/json"},
                timeout=invocation.limits.timeout_seconds,
                max_bytes=_stream_response_limit(self.profile, invocation),
                body=body,
            )
        ) as frames:
            async for payload in frames:
                for delta in decoder.accept(payload):
                    await emit_text(delta)
        return self._checked_result(decoder.complete())

    def _checked_result(self, result: CompletionResult) -> CompletionResult:
        if result.effective_model is not None and result.effective_model != self.profile.model:
            raise RuntimeFailure(
                "provider_model_mismatch",
                "The provider returned a different model",
                status_code=502,
            )
        # The endpoint does not report which effort it applied, so it stays unknown.
        return result
