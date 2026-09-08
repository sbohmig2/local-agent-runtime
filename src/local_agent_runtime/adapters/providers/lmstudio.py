"""LM Studio reasoning adapter and local model-catalog semantics."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar

from local_agent_runtime.adapters.chat_codec import chat_body, decode_chat
from local_agent_runtime.adapters.http_transport import ClientFactory, default_client, request_json
from local_agent_runtime.configuration import validate_connection
from local_agent_runtime.contracts import (
    Capabilities,
    CompletionResult,
    HealthStatus,
    Invocation,
    ModelDiscovery,
    ModelProfile,
    ProviderConnection,
    ProviderHealth,
    ReasoningEffort,
)
from local_agent_runtime.errors import RuntimeFailure
from local_agent_runtime.reasoning import resolve_efforts


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
        except RuntimeFailure:
            # Older servers expose only /v1/models: loaded state stays unknown.
            pass
        return ModelDiscovery(supported=True, models=models, loaded_models=loaded)

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
            body=chat_body(self.profile, invocation),
        )
        result = decode_chat(payload, invocation)
        if result.effective_model is not None and result.effective_model != self.profile.model:
            raise RuntimeFailure(
                "provider_model_mismatch",
                "The provider returned a different model",
                status_code=502,
            )
        # The endpoint does not report which effort it applied, so it stays unknown.
        return result
