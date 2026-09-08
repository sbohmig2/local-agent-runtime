"""Provider-neutral local agent runtime."""

from local_agent_runtime.bootstrap import build_runtime
from local_agent_runtime.contracts import (
    Capabilities,
    CompletionResult,
    Invocation,
    Limits,
    Message,
    ModelDiscovery,
    ModelProfile,
    ProcessingClass,
    ProviderConnection,
    ProviderHealth,
    ReasoningEffort,
    RuntimeConfiguration,
    SessionEvent,
    SessionStatus,
    ToolDefinition,
    ToolRequest,
    ToolResult,
)
from local_agent_runtime.embeddings import (
    EmbeddingLimits,
    EmbeddingProfile,
    EmbeddingPurpose,
    EmbeddingResult,
    EmbeddingService,
)
from local_agent_runtime.errors import RuntimeFailure
from local_agent_runtime.gateway import create_app
from local_agent_runtime.service import RuntimeService
from local_agent_runtime.version import PACKAGE_VERSION

__version__ = PACKAGE_VERSION

__all__ = [
    "Capabilities",
    "CompletionResult",
    "EmbeddingLimits",
    "EmbeddingProfile",
    "EmbeddingPurpose",
    "EmbeddingResult",
    "EmbeddingService",
    "Invocation",
    "Limits",
    "Message",
    "ModelDiscovery",
    "ModelProfile",
    "ProcessingClass",
    "ProviderConnection",
    "ProviderHealth",
    "ReasoningEffort",
    "RuntimeConfiguration",
    "RuntimeFailure",
    "RuntimeService",
    "SessionEvent",
    "SessionStatus",
    "ToolDefinition",
    "ToolRequest",
    "ToolResult",
    "__version__",
    "build_runtime",
    "create_app",
]
