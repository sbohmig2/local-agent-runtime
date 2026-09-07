"""LM Studio text embeddings; loopback only, no credential or routing inheritance."""

from dataclasses import dataclass

from local_agent_runtime.adapters.embedding_codec import decode_embeddings
from local_agent_runtime.adapters.http_transport import ClientFactory, default_client, request_json
from local_agent_runtime.configuration import validate_connection
from local_agent_runtime.contracts import ProviderConnection
from local_agent_runtime.embeddings import EmbeddingProfile, EmbeddingResult
from local_agent_runtime.errors import invalid_configuration


@dataclass
class LMStudioEmbeddingAdapter:
    connection: ProviderConnection
    profile: EmbeddingProfile
    client_factory: ClientFactory = default_client

    def __post_init__(self) -> None:
        validate_connection(self.connection)
        if self.connection.driver != "lmstudio" or self.profile.provider_id != self.connection.id:
            raise invalid_configuration("LM Studio embedding connection is invalid")

    async def embed(self, inputs: tuple[str, ...]) -> EmbeddingResult:
        payload = await request_json(
            self.client_factory,
            "POST",
            f"{self.connection.endpoint}/embeddings",
            headers={"Content-Type": "application/json"},
            timeout=self.profile.limits.timeout_seconds,
            max_bytes=self.profile.limits.max_response_bytes,
            body={"model": self.profile.model, "input": list(inputs), "encoding_format": "float"},
        )
        return decode_embeddings(payload, len(inputs), self.profile.dimensions)
