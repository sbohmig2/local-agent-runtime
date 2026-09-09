"""Provider-neutral application services and bounded in-memory sessions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from typing import Any

from jsonschema import ValidationError, validate

from local_agent_runtime.contracts import (
    HealthStatus,
    Invocation,
    Message,
    ModelDiscovery,
    ModelOptionPolicy,
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
    utc_now,
)
from local_agent_runtime.embeddings import EmbeddingService
from local_agent_runtime.errors import RuntimeFailure, conflict, invalid_request, not_found
from local_agent_runtime.ports import (
    ActivationPort,
    AdapterCatalogPort,
    ProviderFactory,
    SelectionPort,
)
from local_agent_runtime.validation import checked_schema, json_text, validate_output

TOOL_NAME = re.compile(r"^[a-zA-Z][a-zA-Z0-9_.:-]{0,127}$")
TOOL_REQUEST_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}$")
MAX_TOOLS = 128
MAX_EVENTS = 512
MAX_TOOL_RESULT_CHARS = 100_000
PROBE_TIMEOUT_SECONDS: float = 20


@dataclass
class SessionRecord:
    id: str
    profile: ModelProfile
    connection: ProviderConnection
    tools: tuple[ToolDefinition, ...]
    messages: list[Message]
    private_processing: bool
    model_option_id: str | None = None
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
    requested_reasoning_effort: ReasoningEffort | None = None
    effective_reasoning_effort: ReasoningEffort | None = None
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
            "model_option_id": self.model_option_id,
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
            "requested_reasoning_effort": (
                self.requested_reasoning_effort.value
                if self.requested_reasoning_effort is not None
                else None
            ),
            "effective_reasoning_effort": (
                self.effective_reasoning_effort.value
                if self.effective_reasoning_effort is not None
                else None
            ),
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
        activation: ActivationPort | None = None,
        adapter_catalog: AdapterCatalogPort | None = None,
    ) -> None:
        self.configuration = configuration
        self.selection = selection
        self.embeddings = embeddings
        self.provider_factory = provider_factory
        self.sessions: dict[str, SessionRecord] = {}
        self._selection_lock = asyncio.Lock()
        self.activation = activation
        self.adapter_catalog = adapter_catalog
        self._activation_state = dict(activation.read()) if activation is not None else {}
        if any(
            key not in configuration.managed_profiles or type(value) is not bool
            for key, value in self._activation_state.items()
        ) or any(
            not self._enabled(profile_id)
            for profile_id in (configuration.default_profile, *configuration.task_routes.values())
        ):
            raise RuntimeFailure(
                "activation_unavailable", "Runtime activation policy changed", status_code=503
            )

    def _enabled(self, profile_id: str) -> bool:
        return self._activation_state.get(
            profile_id, self.configuration.managed_profiles.get(profile_id, True)
        )

    def _activation_blocker(self, profile_id: str) -> str | None:
        if profile_id not in self.configuration.managed_profiles or self.activation is None:
            return "operator_managed"
        if profile_id == self.configuration.default_profile:
            return "default_profile"
        if not self._enabled(profile_id):
            return None
        try:
            selected = self.selected_profile()
        except RuntimeFailure as exc:
            if exc.code != "selection_unavailable":
                raise
            # Catalog inspection must remain available to repair stale selection.
            # Dispatch and profile_state still require a valid selected profile.
            selected = None
        if profile_id == selected:
            return "selected_profile"
        if profile_id in self.configuration.task_routes.values():
            return "task_route_in_use"
        now = utc_now()
        if any(
            record.profile.id == profile_id
            and (
                record.status is SessionStatus.RUNNING
                or (now - record.updated_at).total_seconds() <= 1800
            )
            for record in self.sessions.values()
        ):
            return "profile_in_use"
        return None

    async def adapter_state(self, *, probe: bool = False) -> dict[str, Any]:
        if self.adapter_catalog is None:
            raise RuntimeFailure(
                "catalog_unavailable", "Adapter catalog is unavailable", status_code=503
            )
        catalog = self.adapter_catalog
        definitions = catalog.supported()

        async def inspect(driver: str) -> dict[str, Any]:
            checked_at = utc_now().isoformat()
            try:
                async with asyncio.timeout(PROBE_TIMEOUT_SECONDS):
                    result = dict(
                        await catalog.probe(
                            driver,
                            [
                                connection
                                for connection in self.configuration.providers.values()
                                if connection.driver == driver
                            ],
                        )
                    )
                discovery = result.pop("discovery", None)
                return {
                    "probe": {"state": "checked", **result, "checked_at": checked_at},
                    **({"discovery": discovery} if discovery is not None else {}),
                }
            except (Exception, TimeoutError):
                return {
                    "probe": {
                        "state": "failed",
                        "installed": None,
                        "detail_code": "probe_failed",
                        "checked_at": checked_at,
                    }
                }

        probes = (
            await asyncio.gather(*(inspect(item["id"]) for item in definitions)) if probe else []
        )
        adapters = []
        for index, definition in enumerate(definitions):
            profiles = [
                profile
                for profile in self.configuration.profiles.values()
                if self.configuration.providers[profile.provider_id].driver == definition["id"]
            ]
            options = []
            for profile in profiles:
                enabled = self._enabled(profile.id)
                blocker = self._activation_blocker(profile.id)
                options.append(
                    {
                        "id": profile.id,
                        "profile_id": profile.id,
                        "model": profile.model,
                        "enabled": enabled,
                        "can_enable": not enabled and blocker is None,
                        "can_disable": enabled and blocker is None,
                        "blocked_reason": blocker,
                    }
                )
            adapters.append(
                {
                    **definition,
                    "supported": True,
                    "configured": bool(profiles),
                    "enabled": any(self._enabled(profile.id) for profile in profiles),
                    "profile_ids": [profile.id for profile in profiles],
                    "options": options,
                    **(
                        probes[index]
                        if probe
                        else {
                            "probe": {
                                "state": "not_checked",
                                "installed": None,
                                "detail_code": None,
                                "checked_at": None,
                            }
                        }
                    ),
                }
            )
        return {"adapters": adapters}

    async def set_adapter_activation(self, option_id: Any, enabled: Any) -> dict[str, Any]:
        if (
            not isinstance(option_id, str)
            or option_id not in self.configuration.managed_profiles
            or type(enabled) is not bool
        ):
            raise invalid_request("The activation option is unknown or invalid")
        if self.activation is None:
            raise RuntimeFailure(
                "activation_unavailable", "Managed activation is unavailable", status_code=503
            )
        async with self._selection_lock:
            if self._enabled(option_id) != enabled:
                blocker = self._activation_blocker(option_id)
                if blocker is not None:
                    raise RuntimeFailure(
                        blocker,
                        "The configured default profile cannot be disabled"
                        if blocker == "default_profile"
                        else "The profile cannot be disabled while selected or in use",
                        status_code=409,
                    )
                updated = {**self._activation_state, option_id: enabled}
                self.activation.write(updated)
                self._activation_state = updated
        return await self.adapter_state()

    def selected_profile(self) -> str:
        profile_id = self.selection.read(self.configuration.default_profile)
        if profile_id not in self.configuration.profiles or not self._enabled(profile_id):
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
            if not self._enabled(profile_id):
                raise invalid_request("The selected profile is disabled")
            self.selection.write(profile_id)
        return await self.profile_state()

    async def _probe(self, operation: Any, timeout: float, code: str) -> dict[str, Any]:
        """Bound one provider probe. A failing probe is per-profile state, not an outage."""
        try:
            async with asyncio.timeout(timeout):
                return dict((await operation()).public_dict())
        except TimeoutError:
            return {"failed": "probe_timed_out"}
        except RuntimeFailure as exc:
            return {"failed": exc.code}
        except Exception:
            return {"failed": code}

    async def profile_state(
        self, *, include_health: bool = False, include_discovery: bool = False
    ) -> dict[str, Any]:
        selected = self.selected_profile()
        profiles: list[dict[str, Any]] = []
        adapters = {
            profile_id: self.provider_factory(
                self.configuration.providers[profile.provider_id], profile
            )
            for profile_id, profile in self.configuration.profiles.items()
            if self._enabled(profile_id)
        }
        health = await self._health_probes(adapters) if include_health else {}
        discovery = await self._discovery_probes(adapters) if include_discovery else {}
        for profile_id, profile in self.configuration.profiles.items():
            if not self._enabled(profile_id):
                continue
            connection = self.configuration.providers[profile.provider_id]
            adapter = adapters[profile_id]
            efforts = adapter.reasoning_efforts
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
                "reasoning": {
                    "efforts": [effort.value for effort in efforts],
                    "default": (
                        profile.default_reasoning_effort.value
                        if profile.default_reasoning_effort is not None
                        else None
                    ),
                },
            }
            if include_health:
                item["health"] = health[profile_id]
            if include_discovery:
                item["discovery"] = discovery[connection.id]
            profiles.append(item)
        return {"selected_profile": selected, "profiles": profiles}

    async def _health_probes(self, adapters: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        # Readiness is per profile because limits and model identity differ.
        results = await asyncio.gather(
            *(
                self._probe(adapter.health, PROBE_TIMEOUT_SECONDS, "health_probe_failed")
                for adapter in adapters.values()
            )
        )
        return {
            profile_id: value
            if "failed" not in value
            else ProviderHealth(
                HealthStatus.INCONCLUSIVE,
                installed=None,
                authenticated=None,
                compatible=None,
                detail_code=value["failed"],
            ).public_dict()
            for profile_id, value in zip(adapters, results, strict=True)
        }

    async def _discovery_probes(self, adapters: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        # Enumeration belongs to a connection, so one probe serves every profile on it.
        chosen: dict[str, Any] = {}
        for profile_id, adapter in adapters.items():
            chosen.setdefault(self.configuration.profiles[profile_id].provider_id, adapter)
        results = await asyncio.gather(
            *(
                self._probe(
                    adapter.discover_models, PROBE_TIMEOUT_SECONDS, "discovery_probe_failed"
                )
                for adapter in chosen.values()
            )
        )
        return {
            provider_id: value
            if "failed" not in value
            else ModelDiscovery(supported=True, detail_code=value["failed"]).public_dict()
            for provider_id, value in zip(chosen, results, strict=True)
        }

    def _reasoning_effort(
        self, profile: ModelProfile, connection: ProviderConnection, value: Any
    ) -> ReasoningEffort | None:
        """Resolve one turn's effort. Unsupported values fail before any dispatch."""
        supported = self.provider_factory(connection, profile).reasoning_efforts
        if value is None:
            default = profile.default_reasoning_effort
            return default if default in supported else None
        if not isinstance(value, str):
            raise invalid_request("The reasoning effort is invalid")
        try:
            effort = ReasoningEffort(value)
        except ValueError:
            raise invalid_request("The reasoning effort is unknown") from None
        if effort not in supported:
            raise RuntimeFailure(
                "reasoning_effort_unsupported",
                "The selected profile does not support the requested reasoning effort",
            )
        return effort

    async def _resolved_model_option(
        self,
        profile: ModelProfile,
        option_id: Any,
        task_code: str | None = None,
        discovery: ModelDiscovery | None = None,
    ) -> tuple[ModelProfile, str]:
        if not isinstance(option_id, str):
            raise invalid_request("The model option is invalid")
        connection = self.configuration.providers[profile.provider_id]
        if discovery is None:
            adapter = self.provider_factory(connection, profile)
            try:
                async with asyncio.timeout(PROBE_TIMEOUT_SECONDS):
                    discovery = await adapter.discover_models()
            except TimeoutError:
                raise RuntimeFailure(
                    "model_catalog_unavailable", "The model catalog timed out", status_code=503
                ) from None
            except RuntimeFailure as exc:
                raise RuntimeFailure(
                    "model_catalog_unavailable",
                    "The model catalog is unavailable",
                    status_code=503,
                ) from exc
            except Exception as exc:
                raise RuntimeFailure(
                    "model_catalog_unavailable",
                    "The model catalog is unavailable",
                    status_code=503,
                ) from exc
        if not discovery.supported:
            raise RuntimeFailure(
                "model_catalog_unsupported", "The provider does not expose model options"
            )
        if discovery.detail_code not in {None, "maintained_catalog"} and not discovery.details:
            raise RuntimeFailure(
                "model_catalog_unavailable", "The model catalog is unavailable", status_code=503
            )
        policies = self._model_option_policies(profile, discovery)
        policy = policies.get(option_id)
        if policy is None:
            if re.fullmatch(r"model-[a-f0-9]{24}", option_id):
                raise RuntimeFailure(
                    "model_option_unavailable",
                    "The selected model option is no longer available",
                    status_code=409,
                )
            raise invalid_request("The model option is unknown")
        discovered = next(
            (
                item
                for item in discovery.details
                if item.model == policy.model and item.kind.value == "reasoning"
            ),
            None,
        )
        if discovered is None:
            raise RuntimeFailure(
                "model_option_unavailable",
                "The selected model option is no longer available",
                status_code=409,
            )
        if task_code is not None and task_code not in policy.qualified_tasks:
            raise RuntimeFailure(
                "model_option_unqualified", "The model option is not qualified for this task"
            )
        efforts = policy.reasoning_efforts or discovered.reasoning_efforts
        if discovered.reasoning_efforts_known:
            if profile.model_options and any(
                effort not in discovered.reasoning_efforts for effort in efforts
            ):
                raise RuntimeFailure(
                    "model_option_incompatible",
                    "The model option reasoning policy is no longer supported",
                    status_code=409,
                )
            efforts = tuple(effort for effort in efforts if effort in discovered.reasoning_efforts)
        default = policy.default_reasoning_effort or discovered.default_reasoning_effort
        if default not in efforts:
            default = None
        derived = replace(
            profile,
            model=policy.model,
            qualified_tasks=policy.qualified_tasks,
            reasoning_efforts=efforts,
            default_reasoning_effort=default,
            model_options={},
        )
        # Adapter resolution is the final transport check. It can narrow a
        # configured policy but never widen it.
        transport_efforts = self.provider_factory(connection, derived).reasoning_efforts
        if set(transport_efforts) != set(efforts):
            raise RuntimeFailure(
                "model_option_incompatible",
                "The model option reasoning policy is not supported",
                status_code=409,
            )
        return replace(derived, reasoning_efforts=transport_efforts), option_id

    @staticmethod
    def _model_option_policies(
        profile: ModelProfile, discovery: ModelDiscovery
    ) -> Mapping[str, ModelOptionPolicy]:
        if profile.model_options:
            return profile.model_options
        if not profile.catalog_model_tasks:
            return {}
        policies: dict[str, ModelOptionPolicy] = {}
        for item in discovery.details:
            if item.kind.value != "reasoning":
                continue
            digest = hashlib.sha256((profile.id + "\0" + item.model).encode("utf-8")).hexdigest()[
                :24
            ]
            option_id = f"model-{digest}"
            policies[option_id] = ModelOptionPolicy(
                option_id,
                item.model,
                profile.catalog_model_tasks,
                profile.reasoning_efforts,
                profile.default_reasoning_effort,
            )
        return policies

    async def model_options(self, profile_id: Any) -> dict[str, Any]:
        if not isinstance(profile_id, str) or profile_id not in self.configuration.profiles:
            raise not_found("The profile does not exist")
        if not self._enabled(profile_id):
            raise not_found("The profile does not exist")
        profile = self.configuration.profiles[profile_id]
        connection = self.configuration.providers[profile.provider_id]
        try:
            async with asyncio.timeout(PROBE_TIMEOUT_SECONDS):
                discovery = await self.provider_factory(connection, profile).discover_models()
        except Exception:
            discovery = ModelDiscovery(supported=True, detail_code="catalog_unavailable")
        options: list[dict[str, Any]] = []
        for option_id in self._model_option_policies(profile, discovery):
            try:
                derived, _ = await self._resolved_model_option(
                    profile, option_id, discovery=discovery
                )
            except RuntimeFailure:
                continue
            discovered = next(item for item in discovery.details if item.model == derived.model)
            options.append(
                {
                    "id": option_id,
                    "display_name": discovered.display_name,
                    "reasoning": {
                        "efforts": [item.value for item in derived.reasoning_efforts],
                        "default": (
                            derived.default_reasoning_effort.value
                            if derived.default_reasoning_effort is not None
                            else None
                        ),
                    },
                    "qualified_tasks": list(derived.qualified_tasks),
                    "loaded": discovered.loaded,
                }
            )
        return {
            "profile_id": profile_id,
            "supported": discovery.supported,
            "checked_at": utc_now().isoformat(),
            "detail_code": (
                discovery.detail_code
                or ("model_kind_unknown" if discovery.models and not discovery.details else None)
            ),
            "options": options,
        }

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
        reasoning_effort: Any = None,
        model_option_id: Any = None,
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
        if not self._enabled(selected):
            raise invalid_request("The session profile is disabled")
        profile = self.configuration.profiles[selected]
        connection = self.configuration.providers[profile.provider_id]
        resolved_option_id: str | None = None
        if model_option_id is not None:
            profile, resolved_option_id = await self._resolved_model_option(
                profile, model_option_id, task_code
            )
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
            model_option_id=resolved_option_id,
            task_code=task_code,
            output_schema=checked_schema(output_schema) if output_schema is not None else None,
            requested_reasoning_effort=self._reasoning_effort(
                profile, connection, reasoning_effort
            ),
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
                        record.requested_reasoning_effort,
                    )
                )
            record.effective_model = result.effective_model
            record.effective_upstream = result.effective_upstream
            record.effective_reasoning_effort = result.effective_reasoning_effort
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
                        "effective_reasoning_effort": (
                            result.effective_reasoning_effort.value
                            if result.effective_reasoning_effort is not None
                            else None
                        ),
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

    async def continue_session(
        self, session_id: str, prompt: Any, reasoning_effort: Any = None
    ) -> dict[str, Any]:
        record = self._record(session_id)
        if record.status is not SessionStatus.COMPLETED:
            raise conflict("Only a completed session can receive follow-up input")
        if not isinstance(prompt, str) or not prompt.strip():
            raise invalid_request("The follow-up prompt is invalid")
        # Each turn snapshots its own effort; a running turn is never re-targeted.
        # No override continues the conversation at the effort already in use, so a
        # follow-up never silently drops back to the profile default.
        effort = (
            record.requested_reasoning_effort
            if reasoning_effort is None
            else self._reasoning_effort(record.profile, record.connection, reasoning_effort)
        )
        self._ensure_schedulable(record, [*record.messages, Message("user", prompt)])
        record.final_text = None
        record.failure = None
        record.effective_model = None
        record.effective_upstream = None
        record.requested_reasoning_effort = effort
        record.effective_reasoning_effort = None
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
