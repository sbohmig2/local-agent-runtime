"""LM Studio reasoning adapter and local model-catalog semantics."""

from dataclasses import dataclass

from local_agent_runtime.adapters.chat_codec import chat_body, decode_chat
from local_agent_runtime.adapters.http_transport import ClientFactory, default_client, request_json
from local_agent_runtime.configuration import validate_connection
from local_agent_runtime.contracts import (
    Capabilities,
    CompletionResult,
    HealthStatus,
    Invocation,
    ModelProfile,
    ProviderConnection,
    ProviderHealth,
)
from local_agent_runtime.errors import RuntimeFailure


@dataclass
class LMStudioAdapter:
    connection: ProviderConnection
    profile: ModelProfile
    client_factory: ClientFactory = default_client

    def __post_init__(self) -> None:
        validate_connection(self.connection)

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(token_limit_control=True)

    async def health(self) -> ProviderHealth:
        try:
            payload = await request_json(
                self.client_factory,
                "GET",
                f"{self.connection.endpoint}/models",
                headers={},
                timeout=15,
                max_bytes=200_000,
            )
            catalog = payload.get("data")
            matched = isinstance(catalog, list) and any(
                isinstance(item, dict) and item.get("id") == self.profile.model for item in catalog
            )
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
        return result
