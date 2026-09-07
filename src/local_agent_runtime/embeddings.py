"""Embedding contracts and application service; no provider or storage dependencies."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Protocol

from local_agent_runtime.contracts import ProcessingClass, ProviderConnection, utc_now
from local_agent_runtime.errors import RuntimeFailure, invalid_configuration, invalid_request


class EmbeddingPurpose(StrEnum):
    DOCUMENT = "document"
    QUERY = "query"


@dataclass(frozen=True)
class EmbeddingLimits:
    batch_size: int = 24
    max_input_chars: int = 8_000
    max_batch_chars: int = 192_000
    timeout_seconds: int = 120
    max_response_bytes: int = 8_000_000

    def __post_init__(self) -> None:
        bounds = {
            "batch_size": (1, 64),
            "max_input_chars": (1, 16_000),
            "max_batch_chars": (1, 1_024_000),
            "timeout_seconds": (1, 600),
            "max_response_bytes": (1_024, 32_000_000),
        }
        for name, (minimum, maximum) in bounds.items():
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise invalid_configuration("Embedding limits are invalid")


@dataclass(frozen=True)
class EmbeddingProfile:
    id: str
    provider_id: str
    model: str
    dimensions: int
    document_prefix: str = ""
    query_prefix: str = ""
    # Owner-assigned revision: model aliases alone cannot identify changing weights.
    revision: str = "1"
    normalization: str = "provider"
    distance_metric: str = "cosine"
    allow_external_processing: bool = False
    allow_private_processing: bool = False
    limits: EmbeddingLimits = field(default_factory=EmbeddingLimits)

    def __post_init__(self) -> None:
        for value in (self.id, self.provider_id, self.model, self.revision):
            if (
                not isinstance(value, str)
                or not value
                or value != value.strip()
                or len(value) > 256
                or any(ord(char) < 32 for char in value)
            ):
                raise invalid_configuration("Embedding profile identity is invalid")
        if type(self.dimensions) is not int or not 1 <= self.dimensions <= 16_384:
            raise invalid_configuration("Embedding dimensions are invalid")
        for prefix in (self.document_prefix, self.query_prefix):
            if not isinstance(prefix, str) or len(prefix) > 2_000:
                raise invalid_configuration("Embedding prefixes are invalid")
        if self.normalization not in {"provider", "l2"}:
            raise invalid_configuration("Embedding normalization is unsupported")
        if self.distance_metric not in {"cosine", "dot", "euclidean"}:
            raise invalid_configuration("Embedding distance metric is unsupported")
        if (
            type(self.allow_external_processing) is not bool
            or type(self.allow_private_processing) is not bool
        ):
            raise invalid_configuration("Embedding processing flags are invalid")

    def fingerprint(self, connection: ProviderConnection) -> str:
        """Vector-space identity, excluding secrets, limits and display/profile names.

        Consumer indexes must additionally version their chunking/preprocessing.
        This runtime never owns a consumer's chunking algorithm or index generation.
        """
        identity = {
            "contract": "lar-embedding-space-v1",
            "driver": connection.driver,
            "endpoint": connection.endpoint,
            "upstream": connection.upstream,
            "model": self.model,
            "revision": self.revision,
            "dimensions": self.dimensions,
            "document_prefix": self.document_prefix,
            "query_prefix": self.query_prefix,
            "normalization": self.normalization,
            "distance_metric": self.distance_metric,
        }
        return hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: tuple[tuple[float, ...], ...]
    effective_model: str | None
    effective_upstream: str | None = None
    usage: Mapping[str, int | float] = field(default_factory=dict)


class EmbeddingProviderPort(Protocol):
    """One bounded batch. Inputs have already received their purpose-specific prefix."""

    async def embed(self, inputs: tuple[str, ...]) -> EmbeddingResult: ...


EmbeddingProviderFactory = Callable[[ProviderConnection, EmbeddingProfile], EmbeddingProviderPort]


def checked_vectors(
    vectors: object, *, count: int, dimensions: int, normalization: str
) -> tuple[tuple[float, ...], ...]:
    """Reject malformed, non-finite, boolean, zero, or wrong-sized vectors."""
    failure = RuntimeFailure(
        "invalid_embedding_response", "Embedding vectors are invalid", status_code=502
    )
    if not isinstance(vectors, (list, tuple)) or len(vectors) != count:
        raise failure
    validated: list[tuple[float, ...]] = []
    for vector in vectors:
        if not isinstance(vector, (list, tuple)) or len(vector) != dimensions:
            raise failure
        try:
            if any(type(value) not in (int, float) or not math.isfinite(value) for value in vector):
                raise failure
            values = tuple(float(value) for value in vector)
            norm = math.hypot(*values)
            if norm == 0 or not math.isfinite(norm):
                raise failure
            validated.append(
                tuple(value / norm for value in values) if normalization == "l2" else values
            )
        except (TypeError, ValueError, OverflowError):
            raise failure from None
    return tuple(validated)


class EmbeddingService:
    def __init__(
        self,
        providers: Mapping[str, ProviderConnection],
        profiles: Mapping[str, EmbeddingProfile],
        *,
        provider_factory: EmbeddingProviderFactory,
        max_concurrency: int = 4,
    ) -> None:
        self.providers = providers
        self.profiles = profiles
        self.provider_factory = provider_factory
        if type(max_concurrency) is not int or not 1 <= max_concurrency <= 32:
            raise invalid_configuration("Embedding concurrency is invalid")
        self._max_concurrency = max_concurrency
        self._active = 0

    def profile_state(self) -> dict[str, object]:
        return {
            "profiles": [
                {
                    "id": profile.id,
                    "provider_id": profile.provider_id,
                    "driver": self.providers[profile.provider_id].driver,
                    "model": profile.model,
                    "dimensions": profile.dimensions,
                    "processing": self.providers[profile.provider_id].processing.value,
                    "profile_fingerprint": profile.fingerprint(self.providers[profile.provider_id]),
                    "document_prefix": profile.document_prefix,
                    "query_prefix": profile.query_prefix,
                    "revision": profile.revision,
                    "normalization": profile.normalization,
                    "distance_metric": profile.distance_metric,
                    "limits": asdict(profile.limits),
                    "allow_private_processing": profile.allow_private_processing,
                    "capabilities": {"text_embeddings": True, "batching": True, "streaming": False},
                }
                for profile in self.profiles.values()
            ]
        }

    async def embed(
        self,
        profile_id: str,
        inputs: Sequence[str],
        *,
        purpose: EmbeddingPurpose,
        expected_fingerprint: str | None = None,
        allow_external_processing: bool = False,
        private_processing: bool = False,
    ) -> dict[str, object]:
        if not isinstance(profile_id, str) or profile_id not in self.profiles:
            raise invalid_request("The embedding profile is unknown")
        profile = self.profiles[profile_id]
        connection = self.providers[profile.provider_id]
        if type(allow_external_processing) is not bool or type(private_processing) is not bool:
            raise invalid_request("Embedding processing flags are invalid")
        if (
            connection.processing is ProcessingClass.EXTERNAL
            and not (profile.allow_external_processing and allow_external_processing)
        ) or (private_processing and not profile.allow_private_processing):
            raise RuntimeFailure(
                "processing_not_allowed", "Embedding processing is not allowed", status_code=403
            )
        fingerprint = profile.fingerprint(connection)
        if expected_fingerprint is not None and expected_fingerprint != fingerprint:
            raise RuntimeFailure(
                "embedding_profile_mismatch",
                "The embedding space does not match the index",
                status_code=409,
            )
        if not isinstance(purpose, EmbeddingPurpose):
            raise invalid_request("Embedding purpose must be document or query")
        if isinstance(inputs, (str, bytes)) or not isinstance(inputs, (list, tuple)):
            raise invalid_request("Embedding inputs must be a batch of text")
        if not 1 <= len(inputs) <= profile.limits.batch_size:
            raise invalid_request("Embedding batch size exceeds its bounds")
        prefix = (
            profile.document_prefix
            if purpose is EmbeddingPurpose.DOCUMENT
            else profile.query_prefix
        )
        if any(
            not isinstance(value, str)
            or not value.strip()
            or len(prefix) + len(value) > profile.limits.max_input_chars
            for value in inputs
        ):
            raise invalid_request("Embedding text is empty or exceeds its limit")
        prepared = tuple(prefix + value for value in inputs)
        if sum(map(len, prepared)) > profile.limits.max_batch_chars:
            raise invalid_request("Embedding batch exceeds its total input limit")
        if self._active >= self._max_concurrency:
            raise RuntimeFailure(
                "capacity_exceeded", "Embedding concurrency limit reached", status_code=429
            )
        self._active += 1
        started = utc_now()
        try:
            async with asyncio.timeout(profile.limits.timeout_seconds):
                result = await self.provider_factory(connection, profile).embed(prepared)
            vectors = checked_vectors(
                result.vectors,
                count=len(prepared),
                dimensions=profile.dimensions,
                normalization=profile.normalization,
            )
            if result.effective_model is not None and result.effective_model != profile.model:
                raise RuntimeFailure(
                    "embedding_model_mismatch", "The embedding model changed", status_code=502
                )
            return {
                "profile_id": profile.id,
                "provider_id": connection.id,
                "adapter": connection.driver,
                "profile_fingerprint": fingerprint,
                "purpose": purpose.value,
                "requested_model": profile.model,
                "effective_model": result.effective_model,
                "effective_upstream": result.effective_upstream,
                "processing": connection.processing.value,
                "dimensions": profile.dimensions,
                "vectors": [list(vector) for vector in vectors],
                "started_at": started.isoformat(),
                "finished_at": utc_now().isoformat(),
                "usage": dict(result.usage),
                "validation": "passed",
            }
        except TimeoutError:
            raise RuntimeFailure(
                "provider_timeout", "Embedding request timed out", status_code=504
            ) from None
        except RuntimeFailure:
            raise
        except Exception:
            raise RuntimeFailure(
                "provider_failure", "Embedding request failed safely", status_code=502
            ) from None
        finally:
            self._active -= 1
