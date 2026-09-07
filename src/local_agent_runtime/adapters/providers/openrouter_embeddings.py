"""OpenRouter text embeddings, with a pinned upstream and no automatic fallback."""

from dataclasses import dataclass

from local_agent_runtime.adapters.embedding_codec import decode_embeddings
from local_agent_runtime.adapters.http_transport import ClientFactory, default_client, request_json
from local_agent_runtime.adapters.providers.openrouter_policy import (
    headers,
    routing,
    upstream_matches,
)
from local_agent_runtime.configuration import validate_connection
from local_agent_runtime.contracts import ProviderConnection
from local_agent_runtime.embeddings import EmbeddingProfile, EmbeddingResult
from local_agent_runtime.errors import RuntimeFailure, invalid_configuration


@dataclass
class OpenRouterEmbeddingAdapter:
    connection: ProviderConnection
    profile: EmbeddingProfile
    client_factory: ClientFactory = default_client

    def __post_init__(self) -> None:
        validate_connection(self.connection)
        if self.connection.driver != "openrouter" or self.profile.provider_id != self.connection.id:
            raise invalid_configuration("OpenRouter embedding connection is invalid")
        routing(self.connection)

    async def embed(self, inputs: tuple[str, ...]) -> EmbeddingResult:
        payload = await request_json(
            self.client_factory,
            "POST",
            f"{self.connection.endpoint}/embeddings",
            headers=headers(self.connection),
            timeout=self.profile.limits.timeout_seconds,
            max_bytes=self.profile.limits.max_response_bytes,
            body={
                "model": self.profile.model,
                "input": list(inputs),
                "encoding_format": "float",
                "provider": routing(self.connection),
            },
        )
        result = decode_embeddings(payload, len(inputs), self.profile.dimensions)
        if not upstream_matches(self.connection.upstream, result.effective_upstream):
            raise RuntimeFailure(
                "provider_upstream_mismatch",
                "The provider returned a different upstream",
                status_code=502,
            )
        return result
