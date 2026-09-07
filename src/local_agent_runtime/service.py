"""Provider-neutral application services and bounded in-memory sessions."""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from jsonschema import ValidationError, validate

from local_agent_runtime.contracts import (
    Invocation,
    Message,
    ModelProfile,
    ProcessingClass,
    ProviderConnection,
    RuntimeConfiguration,
    SessionEvent,
    SessionStatus,
    ToolDefinition,
    ToolRequest,
    ToolResult,
    utc_now,
)
from local_agent_runtime.embeddings import EmbeddingService
from local_agent_runtime.errors import RuntimeFailure, conflict, invalid_request, not_found
from local_agent_runtime.ports import ProviderFactory, SelectionPort
from local_agent_runtime.validation import checked_schema, json_text, validate_output

TOOL_NAME = re.compile(r"^[a-zA-Z][a-zA-Z0-9_.:-]{0,127}$")
TOOL_REQUEST_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}$")
MAX_TOOLS = 128
MAX_EVENTS = 512
MAX_TOOL_RESULT_CHARS = 100_000


@dataclass
class SessionRecord:
    id: str
    profile: ModelProfile
    connection: ProviderConnection
    tools: tuple[ToolDefinition, ...]
    messages: list[Message]
    private_processing: bool
    task_code: str | None = None
    output_schema: Mapping[str, Any] | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    finished_at: datetime | None = None
    output_chars: int = 0
    seen_tool_ids: set[str] = field(default_factory=set)
    status: SessionStatus = SessionStatus.CREATED
    events: list[SessionEvent] = field(default_factory=list)
    pending_tools: dict[str, ToolRequest] = field(default_factory=dict)
    tool_rounds: int = 0
    final_text: str | None = None
    failure: dict[str, str] | None = None
    effective_model: str | None = None
    effective_upstream: str | None = None
    usage: dict[str, int | float] = field(default_factory=dict)
    validation: str = "pending"
    task: asyncio.Task[None] | None = None

    def add_event(self, event_type: str, payload: Mapping[str, Any] | None = None) -> None:
        self.updated_at = utc_now()
        if self.status in {SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELED}:
            self.finished_at = self.updated_at
        if len(self.events) >= MAX_EVENTS:
            raise RuntimeFailure("event_limit_exceeded", "The session event limit was reached")
        self.events.append(SessionEvent(len(self.events) + 1, event_type, utc_now(), payload or {}))

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "profile_id": self.profile.id,
            "provider_id": self.connection.id,
            "adapter": self.connection.driver,
            "requested_model": self.profile.model,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "processing": self.connection.processing.value,
            "task_code": self.task_code,
            "status": self.status.value,
            "pending_tools": [request.public_dict() for request in self.pending_tools.values()],
            "tool_rounds": self.tool_rounds,
            "final_text": self.final_text,
            "failure": self.failure,
            "effective_model": self.effective_model,
            "effective_upstream": self.effective_upstream,
            "usage": dict(self.usage),
            "limits": asdict(self.profile.limits),
            "validation": self.validation,
            "event_count": len(self.events),
        }


class RuntimeService:
    def __init__(
        self,
        configuration: RuntimeConfiguration,
        selection: SelectionPort,
        *,
        provider_factory: ProviderFactory,
        embeddings: EmbeddingService | None = None,
    ) -> None:
        self.configuration = configuration
        self.selection = selection
        self.embeddings = embeddings
        self.provider_factory = provider_factory
        self.sessions: dict[str, SessionRecord] = {}
        self._selection_lock = asyncio.Lock()

    def selected_profile(self) -> str:
        profile_id = self.selection.read(self.configuration.default_profile)
        if profile_id not in self.configuration.profiles:
            raise RuntimeFailure(
                "selection_unavailable",
                "The selected profile is no longer configured",
                status_code=503,
            )
        return profile_id

    async def select_profile(self, profile_id: Any) -> dict[str, Any]:
        if not isinstance(profile_id, str) or profile_id not in self.configuration.profiles:
            raise invalid_request("The selected profile is unknown")
        async with self._selection_lock:
            self.selection.write(profile_id)
        return await self.profile_state()

    async def profile_state(self, *, include_health: bool = False) -> dict[str, Any]:
        selected = self.selected_profile()
        profiles: list[dict[str, Any]] = []
        for profile_id, profile in self.configuration.profiles.items():
            connection = self.configuration.providers[profile.provider_id]
            adapter = self.provider_factory(connection, profile)
            item: dict[str, Any] = {
                "id": profile_id,
                "provider_id": connection.id,
                "driver": connection.driver,
                "model": profile.model,
                "configured": True,
                "processing": connection.processing.value,
                "allow_private_processing": profile.allow_private_processing,
                "qualified_tasks": list(profile.qualified_tasks),
                "qualification": {
                    "status": "qualified" if profile.qualified_tasks else "unqualified",
                    "tasks": list(profile.qualified_tasks),
                },
                "selected": profile_id == selected,
                "capabilities": adapter.capabilities.public_dict(),
            }
            if include_health:
                item["health"] = (await adapter.health()).public_dict()
            profiles.append(item)
        return {"selected_profile": selected, "profiles": profiles}

    @staticmethod
    def _tools(value: Sequence[ToolDefinition]) -> tuple[ToolDefinition, ...]:
        if len(value) > MAX_TOOLS:
            raise invalid_request("The tool catalog is too large")
        names: set[str] = set()
        tools: list[ToolDefinition] = []
        for tool in value:
            if not TOOL_NAME.fullmatch(tool.name) or tool.name in names:
                raise invalid_request("The tool catalog contains an invalid or duplicate name")
            if not isinstance(tool.description, str) or len(tool.description) > 4_000:
                raise invalid_request("A tool description is invalid")
            schema = checked_schema(tool.input_schema)
            if schema.get("type") != "object":
                raise invalid_request("Tool input schemas must describe objects")
            names.add(tool.name)
            tools.append(ToolDefinition(tool.name, tool.description, schema))
        return tuple(tools)

    async def create_session(
        self,
        prompt: Any,
        tools: Sequence[ToolDefinition] = (),
        *,
        instructions: Any = None,
        profile_id: str | None = None,
        task_code: str | None = None,
        private_processing: bool = False,
        allow_external_processing: bool = False,
        output_schema: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(prompt, str) or not prompt.strip():
            raise invalid_request("The session prompt is invalid")
        if instructions is not None and (
            not isinstance(instructions, str) or not instructions.strip()
        ):
            raise invalid_request("The session instructions are invalid")
        if type(private_processing) is not bool or type(allow_external_processing) is not bool:
            raise invalid_request("Processing flags are invalid")
        if task_code is not None and not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", task_code):
            raise invalid_request("The task code is invalid")
        routed = self.configuration.task_routes.get(task_code) if task_code else None
        if task_code is not None and routed is None:
            raise invalid_request("The task route is not configured")
        if profile_id is not None and routed is not None and profile_id != routed:
            raise invalid_request("The explicit profile conflicts with the configured task route")
        selected = profile_id or routed or self.selected_profile()
        if selected not in self.configuration.profiles:
            raise invalid_request("The session profile is unknown")
        profile = self.configuration.profiles[selected]
        connection = self.configuration.providers[profile.provider_id]
        if connection.processing is ProcessingClass.EXTERNAL and not (
            profile.allow_external_processing and allow_external_processing
        ):
            raise RuntimeFailure(
                "processing_not_allowed", "External processing is not allowed", status_code=403
            )
        if len(prompt) > profile.limits.max_input_chars:
            raise invalid_request("The session input exceeds its limit")
        now = utc_now()
        for old_id, old in tuple(self.sessions.items()):
            if (
                old.status is not SessionStatus.RUNNING
                and (now - old.updated_at).total_seconds() > 1800
            ):
                del self.sessions[old_id]
        if len(self.sessions) >= 128:
            raise RuntimeFailure("capacity_exceeded", "Session capacity reached", status_code=429)
        if private_processing and not profile.allow_private_processing:
            raise RuntimeFailure(
                "processing_not_allowed",
                "The selected profile does not allow private processing",
                status_code=403,
            )
        messages = []
        if instructions is not None:
            messages.append(Message("system", instructions))
        messages.append(Message("user", prompt))
        record = SessionRecord(
            id=str(uuid.uuid4()),
            profile=profile,
            connection=connection,
            tools=self._tools(tools),
            messages=messages,
            private_processing=private_processing,
            task_code=task_code,
            output_schema=checked_schema(output_schema) if output_schema is not None else None,
        )
        record.add_event("session_created", {"profile_id": profile.id, "task_code": task_code})
        self._ensure_schedulable(record, record.messages)
        self.sessions[record.id] = record
        self._schedule(record)
        return record.public_dict()

    def _record(self, session_id: str) -> SessionRecord:
        record = self.sessions.get(session_id)
        if record is None:
            raise not_found("The session does not exist")
        if (
            utc_now() - record.updated_at
        ).total_seconds() > 1800 and record.status is not SessionStatus.RUNNING:
            del self.sessions[session_id]
            raise not_found("The session expired")
        return record

    def session(self, session_id: str) -> dict[str, Any]:
        return self._record(session_id).public_dict()

    def events(self, session_id: str, after: int = 0) -> list[dict[str, Any]]:
        if type(after) is not int or after < 0:
            raise invalid_request("The event cursor is invalid")
        record = self._record(session_id)
        if after > len(record.events):
            raise invalid_request("The event cursor is ahead of the session")
        return [event.public_dict() for event in record.events if event.sequence > after]

    def _schedule(self, record: SessionRecord) -> None:
        self._ensure_schedulable(record, record.messages)
        record.status = SessionStatus.RUNNING
        record.finished_at = None
        record.add_event("provider_started")
        record.task = asyncio.create_task(self._advance(record))

    def _ensure_schedulable(self, record: SessionRecord, messages: Sequence[Message]) -> None:
        if record.task is not None and not record.task.done():
            raise conflict("The session is already running")
        if len(record.events) >= MAX_EVENTS - 8:
            raise conflict("The session event limit was reached; start a new session")
        active = sum(item.status is SessionStatus.RUNNING for item in self.sessions.values())
        if active >= 8 and record.status is not SessionStatus.RUNNING:
            raise RuntimeFailure(
                "capacity_exceeded", "Provider concurrency limit reached", status_code=429
            )
        json_text([item.public_dict() for item in messages], record.profile.limits.max_input_chars)

    async def _advance(self, record: SessionRecord) -> None:
        adapter = self.provider_factory(record.connection, record.profile)
        try:
            async with asyncio.timeout(record.profile.limits.timeout_seconds):
                result = await adapter.complete(
                    Invocation(
                        tuple(record.messages),
                        record.tools,
                        record.profile.limits,
                        record.output_schema,
                    )
                )
            record.effective_model = result.effective_model
            record.effective_upstream = result.effective_upstream
            for name, count in result.usage.items():
                record.usage[name] = record.usage.get(name, 0) + count
            output_size = len(result.text) + len(
                json.dumps([item.public_dict() for item in result.tool_requests], allow_nan=False)
            )
            if record.output_chars + output_size > record.profile.limits.max_output_chars:
                raise RuntimeFailure(
                    "output_limit_exceeded", "The session output exceeds its limit"
                )
            record.output_chars += output_size
            if result.tool_requests:
                if record.tool_rounds >= record.profile.limits.max_tool_rounds:
                    raise RuntimeFailure(
                        "tool_round_limit_exceeded",
                        "The session exceeded its tool-round limit",
                    )
                catalog = {tool.name: tool for tool in record.tools}
                pending: dict[str, ToolRequest] = {}
                for request in result.tool_requests:
                    if (
                        not TOOL_REQUEST_ID.fullmatch(request.id)
                        or request.id in pending
                        or request.id in record.seen_tool_ids
                    ):
                        raise RuntimeFailure(
                            "invalid_tool_request",
                            "The provider returned a duplicate tool request",
                        )
                    tool = catalog.get(request.name)
                    if tool is None:
                        raise RuntimeFailure(
                            "unknown_tool_request",
                            "The provider requested an unavailable tool",
                        )
                    try:
                        validate(instance=request.arguments, schema=tool.input_schema)
                    except ValidationError:
                        raise RuntimeFailure(
                            "invalid_tool_arguments",
                            "The provider returned invalid tool arguments",
                        ) from None
                    pending[request.id] = request
                record.messages.append(Message("assistant", result.text, tuple(pending.values())))
                record.pending_tools = pending
                record.seen_tool_ids.update(pending)
                record.status = SessionStatus.WAITING_FOR_TOOL
                record.add_event(
                    "tool_requests",
                    {"requests": [request.public_dict() for request in pending.values()]},
                )
            else:
                if record.output_schema is not None:
                    try:
                        value = json.loads(result.text)
                    except (ValueError, RecursionError):
                        raise RuntimeFailure(
                            "schema_validation_failed",
                            "Provider output is not JSON",
                            status_code=502,
                        ) from None
                    validate_output(value, record.output_schema)
                record.messages.append(Message("assistant", result.text))
                record.final_text = result.text
                record.validation = "passed"
                record.status = SessionStatus.COMPLETED
                record.add_event(
                    "session_completed",
                    {
                        "text": result.text,
                        "effective_model": result.effective_model,
                        "effective_upstream": result.effective_upstream,
                        "usage": dict(result.usage),
                    },
                )
        except asyncio.CancelledError:
            record.status = SessionStatus.CANCELED
            record.validation = "not_validated"
            record.add_event("session_canceled")
            raise
        except RuntimeFailure as exc:
            record.status = SessionStatus.FAILED
            record.validation = "failed"
            record.failure = {"code": exc.code, "message": str(exc)}
            record.add_event("session_failed", record.failure)
        except TimeoutError:
            record.status = SessionStatus.FAILED
            record.validation = "failed"
            record.failure = {"code": "provider_timeout", "message": "The provider timed out"}
            record.add_event("session_failed", record.failure)
        except Exception:
            record.status = SessionStatus.FAILED
            record.validation = "failed"
            record.failure = {
                "code": "provider_failure",
                "message": "The provider failed without exposing content",
            }
            record.add_event("session_failed", record.failure)

    async def submit_tool_results(
        self, session_id: str, results: Sequence[ToolResult]
    ) -> dict[str, Any]:
        record = self._record(session_id)
        if record.status is not SessionStatus.WAITING_FOR_TOOL:
            raise conflict("The session is not waiting for tool results")
        supplied = {result.request_id: result for result in results}
        if len(supplied) != len(results) or set(supplied) != set(record.pending_tools):
            raise invalid_request("Tool results must match every pending request exactly")
        messages: list[Message] = []
        for request_id, request in record.pending_tools.items():
            result = supplied[request_id]
            if result.name != request.name:
                raise invalid_request("A tool result name does not match its request")
            try:
                serialized = json.dumps(
                    {"output": result.output, "is_error": result.is_error},
                    separators=(",", ":"),
                    allow_nan=False,
                )
            except (TypeError, ValueError):
                raise invalid_request("A tool result is not valid JSON") from None
            if len(serialized) > MAX_TOOL_RESULT_CHARS:
                raise invalid_request("A tool result exceeds its limit")
            messages.append(
                Message("tool", serialized, tool_request_id=request_id, tool_name=request.name)
            )
        self._ensure_schedulable(record, [*record.messages, *messages])
        record.messages.extend(messages)
        record.pending_tools = {}
        record.tool_rounds += 1
        record.add_event("tool_results_received", {"count": len(results)})
        self._schedule(record)
        return record.public_dict()

    async def continue_session(self, session_id: str, prompt: Any) -> dict[str, Any]:
        record = self._record(session_id)
        if record.status is not SessionStatus.COMPLETED:
            raise conflict("Only a completed session can receive follow-up input")
        if not isinstance(prompt, str) or not prompt.strip():
            raise invalid_request("The follow-up prompt is invalid")
        self._ensure_schedulable(record, [*record.messages, Message("user", prompt)])
        record.final_text = None
        record.failure = None
        record.effective_model = None
        record.effective_upstream = None
        record.validation = "pending"
        record.messages.append(Message("user", prompt))
        record.add_event("user_input_received")
        self._schedule(record)
        return record.public_dict()

    async def cancel(self, session_id: str) -> dict[str, Any]:
        record = self._record(session_id)
        if record.status in {
            SessionStatus.COMPLETED,
            SessionStatus.FAILED,
            SessionStatus.CANCELED,
        }:
            return record.public_dict()
        if record.task is not None and not record.task.done():
            record.task.cancel()
            with suppress(asyncio.CancelledError):
                await record.task
        if record.public_dict()["status"] != SessionStatus.CANCELED.value:
            record.status = SessionStatus.CANCELED
            record.pending_tools = {}
            record.validation = "not_validated"
            record.add_event("session_canceled")
        return record.public_dict()

    async def shutdown(self) -> None:
        for session_id in tuple(self.sessions):
            await self.cancel(session_id)

    async def wait(self, session_id: str) -> dict[str, Any]:
        record = self._record(session_id)
        if record.task is not None:
            with suppress(asyncio.CancelledError):
                await record.task
        return record.public_dict()
