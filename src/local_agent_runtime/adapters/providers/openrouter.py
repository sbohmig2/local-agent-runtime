"""OpenRouter reasoning adapter; credentials and routing never leak into the core."""

from dataclasses import dataclass

from local_agent_runtime.adapters.chat_codec import chat_body, decode_chat
from local_agent_runtime.adapters.http_transport import ClientFactory, default_client, request_json
from local_agent_runtime.adapters.providers.openrouter_policy import (
    headers,
    routing,
    upstream_matches,
)
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
class OpenRouterAdapter:
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
            await request_json(
                self.client_factory,
                "GET",
                f"{self.connection.endpoint}/key",
                headers=headers(self.connection),
                timeout=15,
                max_bytes=200_000,
            )
            return ProviderHealth(
                HealthStatus.INCONCLUSIVE,
                installed=True,
                authenticated=True,
                compatible=None,
                detail_code="model_invocation_not_probed",
            )
        except RuntimeFailure as exc:
            authenticated = (
                False
                if exc.code in {"credential_unavailable", "provider_authentication_failed"}
                else None
            )
            return ProviderHealth(
                HealthStatus.UNAVAILABLE,
                installed=None,
                authenticated=authenticated,
                compatible=None,
                detail_code=exc.code,
            )

    async def complete(self, invocation: Invocation) -> CompletionResult:
        body = chat_body(self.profile, invocation)
        body["provider"] = routing(self.connection)
        payload = await request_json(
            self.client_factory,
            "POST",
            f"{self.connection.endpoint}/chat/completions",
            headers=headers(self.connection),
            timeout=invocation.limits.timeout_seconds,
            max_bytes=max(64_000, invocation.limits.max_output_chars * 6),
            body=body,
        )
        result = decode_chat(payload, invocation)
        if result.effective_model is not None and result.effective_model != self.profile.model:
            raise RuntimeFailure(
                "provider_model_mismatch",
                "The provider returned a different model",
                status_code=502,
            )
        if not upstream_matches(self.connection.upstream, result.effective_upstream):
            raise RuntimeFailure(
                "provider_upstream_mismatch",
                "The provider returned a different upstream",
                status_code=502,
            )
        return result
