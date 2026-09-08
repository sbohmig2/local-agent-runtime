"""Concrete composition kept outside domain and application services."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from local_agent_runtime.adapters.activation import ActivationStore
from local_agent_runtime.adapters.catalog import AdapterCatalog
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
        adapter_catalog=AdapterCatalog(),
        activation=ActivationStore(
            state_root,
            hashlib.sha256(
                json.dumps(
                    {
                        profile_id: {
                            "enabled": enabled,
                            "profile": asdict(configuration.profiles[profile_id]),
                            "connection": asdict(
                                configuration.providers[
                                    configuration.profiles[profile_id].provider_id
                                ]
                            ),
                        }
                        for profile_id, enabled in configuration.managed_profiles.items()
                    },
                    sort_keys=True,
                ).encode()
            ).hexdigest(),
        ),
        embeddings=EmbeddingService(
            configuration.providers,
            configuration.embedding_profiles,
            provider_factory=build_embedding_provider,
        ),
    )
