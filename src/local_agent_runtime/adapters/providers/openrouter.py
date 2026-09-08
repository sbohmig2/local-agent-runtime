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
    ModelDiscovery,
    ModelProfile,
    ProviderConnection,
    ProviderHealth,
    ReasoningEffort,
)
from local_agent_runtime.errors import RuntimeFailure
from local_agent_runtime.reasoning import resolve_efforts


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

    @property
    def reasoning_efforts(self) -> tuple[ReasoningEffort, ...]:
        """Hosted-routing capability expansion is out of scope for this task."""
        return resolve_efforts(self.profile, (), {})

    async def discover_models(self) -> ModelDiscovery:
        return ModelDiscovery(supported=False, detail_code="discovery_not_offered")

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
        if invocation.reasoning_effort is not None:
            raise RuntimeFailure(
                "reasoning_effort_unsupported",
                "The requested reasoning effort is not supported by this profile",
            )
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
