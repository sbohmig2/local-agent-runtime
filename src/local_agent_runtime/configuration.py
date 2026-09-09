"""Strict deployment-local configuration loading."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from local_agent_runtime.contracts import (
    Limits,
    ModelOptionPolicy,
    ModelProfile,
    ProcessingClass,
    ProviderConnection,
    ReasoningEffort,
    RuntimeConfiguration,
)
from local_agent_runtime.embeddings import EmbeddingLimits, EmbeddingProfile
from local_agent_runtime.errors import invalid_configuration

SUPPORTED_DRIVERS = {"codex_cli", "claude_cli", "grok_cli", "lmstudio", "openrouter"}
CLI_COMMANDS = {
    "codex_cli": "codex",
    "claude_cli": "claude",
    "grok_cli": "grok",
}
DEFAULT_ENDPOINTS = {
    "lmstudio": "http://127.0.0.1:1234/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
ENV_REFERENCE = re.compile(r"^env://([A-Z][A-Z0-9_]{0,127})$")


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise invalid_configuration(f"{label} must be an object")
    return value


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise invalid_configuration(f"{label} is invalid")
    return value


def _trusted_command(driver: str, value: Any) -> str:
    expected = CLI_COMMANDS[driver]
    if value is None:
        return expected
    if not isinstance(value, str) or not value:
        raise invalid_configuration("Provider command is invalid")
    if value == expected:
        return value
    candidate = Path(value).expanduser()
    if not candidate.is_absolute() or candidate.name.casefold() not in {
        expected.casefold(),
        f"{expected}.exe".casefold(),
    }:
        raise invalid_configuration(f"{driver} requires the trusted {expected} executable")
    return str(candidate)


def _endpoint(driver: str, value: Any) -> str:
    endpoint = DEFAULT_ENDPOINTS[driver] if value is None else value
    if (
        not isinstance(endpoint, str)
        or endpoint != endpoint.strip()
        or any(ord(c) < 33 for c in endpoint)
    ):
        raise invalid_configuration("Provider endpoint is invalid")
    endpoint = endpoint.rstrip("/")
    try:
        parsed = urlparse(endpoint)
        port = parsed.port
    except ValueError:
        raise invalid_configuration("Provider endpoint is invalid") from None
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise invalid_configuration("Provider endpoints cannot contain credentials or query data")
    if driver == "lmstudio":
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise invalid_configuration("LM Studio must use a loopback HTTP endpoint")
        if parsed.path not in {"", "/v1"} or (port is not None and not 1 <= port <= 65535):
            raise invalid_configuration("LM Studio endpoint path or port is invalid")
        if not parsed.path:
            endpoint += "/v1"
    elif (
        parsed.scheme != "https"
        or parsed.hostname != "openrouter.ai"
        or port not in {None, 443}
        or parsed.path != "/api/v1"
    ):
        raise invalid_configuration("OpenRouter must use https://openrouter.ai")
    return endpoint


def validate_connection(connection: ProviderConnection) -> None:
    """Apply the same trust boundary for Python constructors and YAML consumers."""
    if connection.driver not in SUPPORTED_DRIVERS:
        raise invalid_configuration("Provider driver is unsupported")
    expected = (
        ProcessingClass.LOCAL if connection.driver == "lmstudio" else ProcessingClass.EXTERNAL
    )
    if connection.processing is not expected:
        raise invalid_configuration("Provider processing class is invalid")
    if connection.driver in DEFAULT_ENDPOINTS:
        if (
            _endpoint(connection.driver, connection.endpoint) != connection.endpoint
            or connection.command is not None
        ):
            raise invalid_configuration("Provider endpoint is not normalized")
    else:
        if (
            _trusted_command(connection.driver, connection.command) != connection.command
            or connection.endpoint is not None
        ):
            raise invalid_configuration("Provider command is invalid")
    _credential_reference(connection.driver, connection.credential_ref)
    if connection.driver == "openrouter":
        if not isinstance(connection.upstream, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_./-]{0,127}", connection.upstream
        ):
            raise invalid_configuration("OpenRouter requires an exact upstream provider")
    elif connection.upstream is not None:
        raise invalid_configuration("This provider does not accept upstream routing")


def _credential_reference(driver: str, value: Any) -> str | None:
    if driver != "openrouter":
        if value is not None:
            raise invalid_configuration(f"{driver} does not accept a credential reference")
        return None
    if not isinstance(value, str) or ENV_REFERENCE.fullmatch(value) is None:
        raise invalid_configuration("OpenRouter requires an env:// credential reference")
    return value


def resolve_credential(reference: str | None) -> str | None:
    if reference is None:
        return None
    match = ENV_REFERENCE.fullmatch(reference)
    if match is None:
        raise invalid_configuration("Credential reference is invalid")
    return os.environ.get(match.group(1))


def _limits(value: Any) -> Limits:
    raw = {} if value is None else _mapping(value, "Profile limits")
    allowed = {
        "timeout_seconds",
        "max_input_chars",
        "max_output_chars",
        "max_output_tokens",
        "max_tool_rounds",
    }
    if set(raw) - allowed:
        raise invalid_configuration("Profile limits contain unsupported fields")
    if any(type(value) is not int for value in raw.values()):
        raise invalid_configuration("Profile limits must be integers")
    limits = Limits(**{key: raw[key] for key in raw})
    if not 1 <= limits.timeout_seconds <= 900:
        raise invalid_configuration("Profile timeout is out of bounds")
    if not 1_000 <= limits.max_input_chars <= 200_000:
        raise invalid_configuration("Profile input limit is out of bounds")
    if not 1_000 <= limits.max_output_chars <= 100_000:
        raise invalid_configuration("Profile output limit is out of bounds")
    if not 1 <= limits.max_output_tokens <= 128_000:
        raise invalid_configuration("Profile token limit is out of bounds")
    if not 0 <= limits.max_tool_rounds <= 32:
        raise invalid_configuration("Profile tool-round limit is out of bounds")
    return limits


def _reasoning_efforts(profile_id: str, value: Any) -> tuple[ReasoningEffort, ...]:
    """Operator narrowing only. An adapter still refuses efforts it cannot deliver."""
    if value is None:
        return ()
    if not isinstance(value, list) or not value:
        raise invalid_configuration(f"Profile {profile_id} reasoning efforts are invalid")
    efforts: list[ReasoningEffort] = []
    for item in value:
        if not isinstance(item, str):
            raise invalid_configuration(f"Profile {profile_id} reasoning efforts are invalid")
        try:
            effort = ReasoningEffort(item)
        except ValueError:
            raise invalid_configuration(
                f"Profile {profile_id} declares an unknown reasoning effort"
            ) from None
        if effort in efforts:
            raise invalid_configuration(f"Profile {profile_id} repeats a reasoning effort")
        efforts.append(effort)
    return tuple(efforts)


def _default_reasoning_effort(profile_id: str, value: Any) -> ReasoningEffort | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise invalid_configuration(f"Profile {profile_id} default reasoning effort is invalid")
    try:
        return ReasoningEffort(value)
    except ValueError:
        raise invalid_configuration(
            f"Profile {profile_id} default reasoning effort is unknown"
        ) from None


def _model(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 256
        or value.startswith("-")
        or any(ord(char) < 32 for char in value)
    ):
        raise invalid_configuration(f"{label} is invalid")
    return value


def _model_options(profile_id: str, value: Any) -> Mapping[str, ModelOptionPolicy]:
    raw_options = _mapping({} if value is None else value, f"Profile {profile_id} model options")
    if len(raw_options) > 512:
        raise invalid_configuration(f"Profile {profile_id} has too many model options")
    options: dict[str, ModelOptionPolicy] = {}
    models: set[str] = set()
    for raw_id, value in raw_options.items():
        option_id = _identifier(raw_id, f"Profile {profile_id} model option identifier")
        raw = _mapping(value, f"Profile {profile_id} model option {option_id}")
        allowed = {
            "model",
            "qualified_tasks",
            "reasoning_efforts",
            "default_reasoning_effort",
        }
        if set(raw) - allowed or not {"model", "qualified_tasks"} <= set(raw):
            raise invalid_configuration(
                f"Profile {profile_id} model option {option_id} fields are invalid"
            )
        model = _model(raw.get("model"), f"Profile {profile_id} model option model")
        if model in models:
            raise invalid_configuration(f"Profile {profile_id} repeats a model option")
        tasks = raw.get("qualified_tasks")
        if (
            not isinstance(tasks, list)
            or not tasks
            or any(not isinstance(item, str) or not IDENTIFIER.fullmatch(item) for item in tasks)
            or len(set(tasks)) != len(tasks)
        ):
            raise invalid_configuration(
                f"Profile {profile_id} model option {option_id} qualified tasks are invalid"
            )
        efforts = _reasoning_efforts(
            f"{profile_id} model option {option_id}", raw.get("reasoning_efforts")
        )
        default = _default_reasoning_effort(
            f"{profile_id} model option {option_id}", raw.get("default_reasoning_effort")
        )
        if default is not None and efforts and default not in efforts:
            raise invalid_configuration(
                f"Profile {profile_id} model option {option_id} default reasoning effort "
                "is not declared"
            )
        models.add(model)
        options[option_id] = ModelOptionPolicy(
            option_id,
            model,
            tuple(tasks),
            efforts,
            default,
        )
    return options


def load_configuration(path: Path) -> RuntimeConfiguration:
    try:
        with path.open("rb") as handle:
            content = handle.read(256_001)
        if len(content) > 256_000:
            raise invalid_configuration("Runtime configuration exceeds its limit")
        # Aliases and duplicate keys obscure the effective security policy.
        if any(isinstance(token, yaml.tokens.AliasToken) for token in yaml.scan(content)):
            raise invalid_configuration("Configuration aliases are unsupported")
        data = yaml.load(content, Loader=UniqueKeyLoader)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise invalid_configuration("Unable to read runtime configuration") from exc
    root = _mapping(data, "Configuration")
    required = {
        "version",
        "providers",
        "profiles",
        "default_profile",
        "task_routes",
    }
    if not required <= set(root) or set(root) - required - {
        "embedding_profiles",
        "activation_policy",
    }:
        raise invalid_configuration("Configuration fields are invalid")
    if type(root["version"]) is not int or root["version"] != 1:
        raise invalid_configuration("Unsupported configuration version")

    providers: dict[str, ProviderConnection] = {}
    for raw_id, value in _mapping(root["providers"], "Providers").items():
        provider_id = _identifier(raw_id, "Provider identifier")
        raw = _mapping(value, f"Provider {provider_id}")
        driver = raw.get("driver")
        if not isinstance(driver, str) or driver not in SUPPORTED_DRIVERS:
            raise invalid_configuration(f"Provider {provider_id} has an unsupported driver")
        allowed = (
            {"driver", "command"}
            if driver in CLI_COMMANDS
            else {
                "driver",
                "endpoint",
                "credential_ref",
                "upstream",
            }
        )
        if set(raw) - allowed:
            raise invalid_configuration(f"Provider {provider_id} contains unsupported fields")
        processing = ProcessingClass.LOCAL if driver == "lmstudio" else ProcessingClass.EXTERNAL
        providers[provider_id] = ProviderConnection(
            id=provider_id,
            driver=driver,
            processing=processing,
            command=_trusted_command(driver, raw.get("command"))
            if driver in CLI_COMMANDS
            else None,
            endpoint=_endpoint(driver, raw.get("endpoint"))
            if driver in DEFAULT_ENDPOINTS
            else None,
            credential_ref=_credential_reference(driver, raw.get("credential_ref")),
            upstream=raw.get("upstream"),
        )
        validate_connection(providers[provider_id])
    if not providers:
        raise invalid_configuration("At least one provider is required")

    profiles: dict[str, ModelProfile] = {}
    for raw_id, value in _mapping(root["profiles"], "Profiles").items():
        profile_id = _identifier(raw_id, "Profile identifier")
        raw = _mapping(value, f"Profile {profile_id}")
        allowed = {
            "provider",
            "model",
            "allow_external_processing",
            "allow_private_processing",
            "limits",
            "qualified_tasks",
            "reasoning_efforts",
            "default_reasoning_effort",
            "model_options",
            "catalog_model_tasks",
        }
        if set(raw) - allowed:
            raise invalid_configuration(f"Profile {profile_id} contains unsupported fields")
        provider_id = _identifier(raw.get("provider"), "Profile provider")
        if provider_id not in providers:
            raise invalid_configuration(f"Profile {profile_id} references an unknown provider")
        model = _model(raw.get("model"), f"Profile {profile_id} model")
        external = raw.get("allow_external_processing")
        private = raw.get("allow_private_processing", False)
        if type(external) is not bool or type(private) is not bool:
            raise invalid_configuration(f"Profile {profile_id} processing flags are invalid")
        if providers[provider_id].processing is ProcessingClass.EXTERNAL and not external:
            raise invalid_configuration(
                f"Profile {profile_id} must explicitly allow external processing"
            )
        tasks = raw.get("qualified_tasks", [])
        if (
            not isinstance(tasks, list)
            or any(not isinstance(item, str) or not IDENTIFIER.fullmatch(item) for item in tasks)
            or len(set(tasks)) != len(tasks)
        ):
            raise invalid_configuration(f"Profile {profile_id} qualified tasks are invalid")
        catalog_tasks = raw.get("catalog_model_tasks", [])
        if (
            not isinstance(catalog_tasks, list)
            or any(
                not isinstance(item, str) or not IDENTIFIER.fullmatch(item)
                for item in catalog_tasks
            )
            or len(set(catalog_tasks)) != len(catalog_tasks)
        ):
            raise invalid_configuration(f"Profile {profile_id} catalog model tasks are invalid")
        model_options = _model_options(profile_id, raw.get("model_options"))
        if catalog_tasks and model_options:
            raise invalid_configuration(
                f"Profile {profile_id} cannot mix catalog and exact model option policies"
            )
        efforts = _reasoning_efforts(profile_id, raw.get("reasoning_efforts"))
        default_effort = _default_reasoning_effort(profile_id, raw.get("default_reasoning_effort"))
        if default_effort is not None and efforts and default_effort not in efforts:
            raise invalid_configuration(
                f"Profile {profile_id} default reasoning effort is not declared"
            )
        profiles[profile_id] = ModelProfile(
            id=profile_id,
            provider_id=provider_id,
            model=model,
            allow_external_processing=external,
            allow_private_processing=private,
            limits=_limits(raw.get("limits")),
            qualified_tasks=tuple(tasks),
            reasoning_efforts=efforts,
            default_reasoning_effort=default_effort,
            model_options=model_options,
            catalog_model_tasks=tuple(catalog_tasks),
        )
    if not profiles:
        raise invalid_configuration("At least one profile is required")
    default_profile = _identifier(root["default_profile"], "Default profile")
    if default_profile not in profiles:
        raise invalid_configuration("Default profile is unknown")
    task_routes: dict[str, str] = {}
    for raw_task, raw_profile in _mapping(root["task_routes"], "Task routes").items():
        task_code = _identifier(raw_task, "Task route")
        profile_id = _identifier(raw_profile, "Task route profile")
        if profile_id not in profiles:
            raise invalid_configuration(f"Task route {task_code} references an unknown profile")
        task_routes[task_code] = profile_id
    embedding_profiles: dict[str, EmbeddingProfile] = {}
    for raw_id, value in _mapping(root.get("embedding_profiles", {}), "Embedding profiles").items():
        profile_id = _identifier(raw_id, "Embedding profile identifier")
        raw = dict(_mapping(value, "Embedding profile"))
        allowed = {
            "provider",
            "model",
            "dimensions",
            "document_prefix",
            "query_prefix",
            "revision",
            "normalization",
            "distance_metric",
            "allow_external_processing",
            "allow_private_processing",
            "limits",
        }
        if set(raw) - allowed or not {"provider", "model", "dimensions"} <= set(raw):
            raise invalid_configuration("Embedding profile fields are invalid")
        provider_id = _identifier(raw.pop("provider"), "Embedding provider")
        if provider_id not in providers or providers[provider_id].driver not in {
            "lmstudio",
            "openrouter",
        }:
            raise invalid_configuration("Provider does not support embeddings")
        limits = dict(_mapping(raw.pop("limits", {}), "Embedding limits"))
        if set(limits) - set(EmbeddingLimits.__dataclass_fields__):
            raise invalid_configuration("Embedding limits contain unsupported fields")
        profile = EmbeddingProfile(
            id=profile_id, provider_id=provider_id, limits=EmbeddingLimits(**limits), **raw
        )
        if (
            providers[provider_id].processing is ProcessingClass.EXTERNAL
            and not profile.allow_external_processing
        ):
            raise invalid_configuration(
                "Embedding profile must explicitly allow external processing"
            )
        embedding_profiles[profile_id] = profile
    policy = _mapping(root.get("activation_policy", {}), "Activation policy")
    if set(policy) - {"managed_profiles"}:
        raise invalid_configuration("Activation policy fields are invalid")
    managed = dict(_mapping(policy.get("managed_profiles", {}), "Managed profiles"))
    if any(key not in profiles or type(value) is not bool for key, value in managed.items()):
        raise invalid_configuration("Managed profiles must name configured profiles and booleans")
    if managed.get(default_profile) is False:
        raise invalid_configuration("The default profile must be enabled")
    if any(managed.get(profile_id) is False for profile_id in task_routes.values()):
        raise invalid_configuration("Task routes must be enabled")
    return RuntimeConfiguration(
        providers, profiles, default_profile, task_routes, embedding_profiles, managed
    )


class UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(self, node: yaml.nodes.MappingNode, deep: bool = False) -> dict[Any, Any]:
        keys: set[object] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in keys:
                raise invalid_configuration("Configuration contains invalid or duplicate keys")
            keys.add(key)
        return super().construct_mapping(node, deep=deep)
