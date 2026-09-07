"""OpenAI-compatible embedding wire decoding; shared protocol, not provider policy."""

from collections.abc import Mapping
from typing import Any

from local_agent_runtime.adapters.http_transport import response_identity, safe_usage
from local_agent_runtime.embeddings import EmbeddingResult, checked_vectors
from local_agent_runtime.errors import RuntimeFailure


def decode_embeddings(payload: Mapping[str, Any], count: int, dimensions: int) -> EmbeddingResult:
    failure = RuntimeFailure(
        "invalid_embedding_response", "Embedding response is invalid", status_code=502
    )
    data = payload.get("data")
    if not isinstance(data, list) or len(data) != count:
        raise failure
    ordered: dict[int, object] = {}
    for item in data:
        if not isinstance(item, dict) or type(item.get("index")) is not int:
            raise failure
        index = item["index"]
        if index in ordered or not 0 <= index < count:
            raise failure
        ordered[index] = item.get("embedding")
    vectors = checked_vectors(
        [ordered[index] for index in range(count)],
        count=count,
        dimensions=dimensions,
        normalization="provider",
    )
    return EmbeddingResult(
        vectors,
        response_identity(payload.get("model")),
        response_identity(payload.get("provider")),
        safe_usage(payload.get("usage")),
    )
