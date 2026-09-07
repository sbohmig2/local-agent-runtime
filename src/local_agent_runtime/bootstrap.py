"""Concrete composition kept outside domain and application services."""

from __future__ import annotations

from pathlib import Path

from local_agent_runtime.adapters.selection import SelectionStore
from local_agent_runtime.configuration import load_configuration
from local_agent_runtime.embeddings import EmbeddingService
from local_agent_runtime.providers import build_embedding_provider, build_provider
from local_agent_runtime.service import RuntimeService


def build_runtime(configuration_path: Path, state_root: Path) -> RuntimeService:
    configuration = load_configuration(configuration_path)
    return RuntimeService(
        configuration,
        SelectionStore(state_root),
        provider_factory=build_provider,
        embeddings=EmbeddingService(
            configuration.providers,
            configuration.embedding_profiles,
            provider_factory=build_embedding_provider,
        ),
    )
