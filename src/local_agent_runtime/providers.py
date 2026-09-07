"""Composition registry. Provider behavior belongs to individual adapters."""

from collections.abc import Mapping

from local_agent_runtime.adapters.providers.claude import ClaudeAdapter
from local_agent_runtime.adapters.providers.codex import CodexAdapter
from local_agent_runtime.adapters.providers.grok import GrokAdapter
from local_agent_runtime.adapters.providers.lmstudio import LMStudioAdapter
from local_agent_runtime.adapters.providers.lmstudio_embeddings import LMStudioEmbeddingAdapter
from local_agent_runtime.adapters.providers.openrouter import OpenRouterAdapter
from local_agent_runtime.adapters.providers.openrouter_embeddings import OpenRouterEmbeddingAdapter
from local_agent_runtime.contracts import ModelProfile, ProviderConnection
from local_agent_runtime.embeddings import (
    EmbeddingProfile,
    EmbeddingProviderFactory,
    EmbeddingProviderPort,
)
from local_agent_runtime.errors import invalid_configuration
from local_agent_runtime.ports import ProviderFactory, ProviderPort

REASONING_PROVIDERS: Mapping[str, ProviderFactory] = {
    "codex_cli": CodexAdapter,
    "claude_cli": ClaudeAdapter,
    "grok_cli": GrokAdapter,
    "lmstudio": LMStudioAdapter,
    "openrouter": OpenRouterAdapter,
}
EMBEDDING_PROVIDERS: Mapping[str, EmbeddingProviderFactory] = {
    "lmstudio": LMStudioEmbeddingAdapter,
    "openrouter": OpenRouterEmbeddingAdapter,
}


def build_provider(connection: ProviderConnection, profile: ModelProfile) -> ProviderPort:
    builder = REASONING_PROVIDERS.get(connection.driver)
    if builder is None or profile.provider_id != connection.id:
        raise invalid_configuration("No matching reasoning adapter is installed")
    return builder(connection, profile)


def build_embedding_provider(
    connection: ProviderConnection, profile: EmbeddingProfile
) -> EmbeddingProviderPort:
    builder = EMBEDDING_PROVIDERS.get(connection.driver)
    if builder is None or profile.provider_id != connection.id:
        raise invalid_configuration("No matching embedding adapter is installed")
    return builder(connection, profile)
