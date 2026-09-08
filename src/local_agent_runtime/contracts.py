"""Provider-neutral immutable runtime contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from local_agent_runtime.embeddings import EmbeddingProfile


def utc_now() -> datetime:
    return datetime.now(UTC)


class ProcessingClass(StrEnum):
    LOCAL = "local"
    EXTERNAL = "external"


class ReasoningEffort(StrEnum):
    """Provider-neutral effort vocabulary. Adapters own which values they deliver."""

    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class HealthStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    INCONCLUSIVE = "inconclusive"


class SessionStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_FOR_TOOL = "waiting_for_tool"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


@dataclass(frozen=True)
class Limits:
    timeout_seconds: int = 180
    max_input_chars: int = 140_000
    max_output_chars: int = 50_000
    max_output_tokens: int = 4_096
    max_tool_rounds: int = 12


@dataclass(frozen=True)
class Capabilities:
    text_generation: bool = True
    structured_output: bool = True
    tool_requests: bool = True
    token_streaming: bool = False
    conversation_continuation: bool = True
    model_discovery: bool = False
    token_limit_control: bool = False
    reasoning_effort_control: bool = False

    def public_dict(self) -> dict[str, bool]:
        return {
            "text_generation": self.text_generation,
            "structured_output": self.structured_output,
            "tool_requests": self.tool_requests,
            "token_streaming": self.token_streaming,
            "conversation_continuation": self.conversation_continuation,
            "model_discovery": self.model_discovery,
            "token_limit_control": self.token_limit_control,
            "reasoning_effort_control": self.reasoning_effort_control,
        }


@dataclass(frozen=True)
class ProviderConnection:
    id: str
    driver: str
    processing: ProcessingClass
    command: str | None = None
    endpoint: str | None = None
    credential_ref: str | None = None
    upstream: str | None = None


@dataclass(frozen=True)
class ModelProfile:
    id: str
    provider_id: str
    model: str
    allow_external_processing: bool
    allow_private_processing: bool
    limits: Limits = field(default_factory=Limits)
    qualified_tasks: tuple[str, ...] = ()
    reasoning_efforts: tuple[ReasoningEffort, ...] = ()
    default_reasoning_effort: ReasoningEffort | None = None


@dataclass(frozen=True)
class RuntimeConfiguration:
    providers: Mapping[str, ProviderConnection]
    profiles: Mapping[str, ModelProfile]
    default_profile: str
    task_routes: Mapping[str, str] = field(default_factory=dict)
    embedding_profiles: Mapping[str, EmbeddingProfile] = field(default_factory=dict)
    managed_profiles: Mapping[str, bool] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderHealth:
    status: HealthStatus
    installed: bool | None
    authenticated: bool | None
    compatible: bool | None
    detail_code: str | None = None
    effective_model: str | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "installed": self.installed,
            "authenticated": self.authenticated,
            "compatible": self.compatible,
            "detail_code": self.detail_code,
            "effective_model": self.effective_model,
        }


@dataclass(frozen=True)
class ModelDiscovery:
    """Bounded model enumeration. Distinct from readiness and from qualification."""

    supported: bool
    models: tuple[str, ...] = ()
    detail_code: str | None = None
    loaded_models: tuple[str, ...] | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "supported": self.supported,
            "models": list(self.models),
            "detail_code": self.detail_code,
            **(
                {"loaded_models": list(self.loaded_models)}
                if self.loaded_models is not None
                else {}
            ),
        }


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: Mapping[str, Any]

    def public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": dict(self.input_schema),
        }


@dataclass(frozen=True)
class ToolRequest:
    id: str
    name: str
    arguments: Mapping[str, Any]

    def public_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": dict(self.arguments)}


@dataclass(frozen=True)
class ToolResult:
    request_id: str
    name: str
    output: Any
    is_error: bool = False


@dataclass(frozen=True)
class Message:
    role: str
    content: str
    tool_requests: tuple[ToolRequest, ...] = ()
    tool_request_id: str | None = None
    tool_name: str | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "tool_requests": [item.public_dict() for item in self.tool_requests],
            "tool_request_id": self.tool_request_id,
            "tool_name": self.tool_name,
        }


@dataclass(frozen=True)
class CompletionResult:
    text: str
    tool_requests: tuple[ToolRequest, ...]
    effective_model: str | None
    effective_upstream: str | None = None
    usage: Mapping[str, int | float] = field(default_factory=dict)
    effective_reasoning_effort: ReasoningEffort | None = None


@dataclass(frozen=True)
class Invocation:
    messages: tuple[Message, ...]
    tools: tuple[ToolDefinition, ...]
    limits: Limits
    output_schema: Mapping[str, Any] | None = None
    reasoning_effort: ReasoningEffort | None = None


@dataclass(frozen=True)
class SessionEvent:
    sequence: int
    type: str
    occurred_at: datetime
    payload: Mapping[str, Any]

    def public_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "type": self.type,
            "occurred_at": self.occurred_at.isoformat(),
            "payload": dict(self.payload),
        }
