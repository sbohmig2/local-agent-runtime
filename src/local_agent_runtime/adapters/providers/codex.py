"""Codex CLI policy: isolated completion and structured model catalog."""

import json
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar

from local_agent_runtime.adapters.cli_base import CLIAdapterBase
from local_agent_runtime.adapters.cli_environment import _provider_environment
from local_agent_runtime.contracts import (
    Capabilities,
    DiscoveredModel,
    ModelDiscovery,
    ModelKind,
    ReasoningEffort,
)
from local_agent_runtime.errors import RuntimeFailure


class CodexAdapter(CLIAdapterBase):
    TRANSPORT_EFFORTS: ClassVar[tuple[ReasoningEffort, ...]] = tuple(ReasoningEffort)
    # `codex exec` forwards an unrecognized level without complaint and each model
    # advertises its own supported levels, so no model is pre-qualified here.
    VERIFIED_EFFORTS: ClassVar[Mapping[str, tuple[ReasoningEffort, ...]]] = {}

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(
            model_discovery=True,
            reasoning_effort_control=bool(self.reasoning_efforts),
        )

    async def discover_models(self) -> ModelDiscovery:
        executable = self.executable()
        if executable is None:
            return ModelDiscovery(supported=True, detail_code="executable_unavailable")
        initialize = json.dumps(
            {
                "method": "initialize",
                "id": 1,
                "params": {
                    "clientInfo": {"name": "local-agent-runtime", "version": "catalog"},
                    "capabilities": {},
                },
            },
            separators=(",", ":"),
        )
        try:
            raw_models: list[object] = []
            cursor: str | None = None
            seen_cursors: set[str] = set()
            for _page in range(8):
                params: dict[str, object] = {"limit": 128}
                if cursor is not None:
                    params["cursor"] = cursor
                model_list = json.dumps(
                    {"method": "model/list", "id": 2, "params": params},
                    separators=(",", ":"),
                )
                with tempfile.TemporaryDirectory(prefix="lar-catalog-") as directory:
                    root = Path(directory)
                    result = await self.json_rpc_runner(
                        [executable, "app-server", "--stdio"],
                        [(1, initialize), (2, model_list)],
                        root,
                        self.environment(root),
                        15,
                    )
                if result.returncode != 0:
                    return ModelDiscovery(supported=True, detail_code="catalog_process_failed")
                payload = _response(result.stdout)
                if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                    return ModelDiscovery(supported=True, detail_code="invalid_provider_response")
                raw_models.extend(payload["data"])
                if len(raw_models) > 512:
                    return ModelDiscovery(supported=True, detail_code="catalog_limit_exceeded")
                next_cursor = payload.get("nextCursor")
                if next_cursor is None:
                    break
                if (
                    not isinstance(next_cursor, str)
                    or not next_cursor
                    or len(next_cursor) > 1024
                    or next_cursor in seen_cursors
                ):
                    return ModelDiscovery(supported=True, detail_code="invalid_provider_response")
                seen_cursors.add(next_cursor)
                cursor = next_cursor
            else:
                return ModelDiscovery(supported=True, detail_code="catalog_limit_exceeded")
            details: list[DiscoveredModel] = []
            seen: set[str] = set()
            for item in raw_models:
                parsed = _catalog_item(item)
                if parsed is None:
                    return ModelDiscovery(supported=True, detail_code="invalid_provider_response")
                if parsed.model in seen:
                    return ModelDiscovery(supported=True, detail_code="invalid_provider_response")
                seen.add(parsed.model)
                if isinstance(item, dict) and not bool(item.get("hidden")):
                    details.append(parsed)
            return ModelDiscovery(
                supported=True,
                models=tuple(item.model for item in details),
                details=tuple(details),
            )
        except RuntimeFailure as exc:
            return ModelDiscovery(supported=True, detail_code=exc.code)

    def environment(self, root: Path) -> dict[str, str]:
        return _provider_environment("CODEX_HOME", "CODEX_CA_CERTIFICATE")

    def auth_arguments(self, executable: str) -> list[str]:
        return [executable, "login", "status"]

    def arguments(
        self, executable: str, root: Path, schema: Path, effort: ReasoningEffort | None
    ) -> list[str]:
        args = [executable, "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules"]
        for feature in (
            "shell_tool",
            "apps",
            "browser_use",
            "computer_use",
            "image_generation",
            "multi_agent",
        ):
            args.extend(("--disable", feature))
        for setting in (
            'web_search="disabled"',
            # Project and user AGENTS.md files are untrusted third-party
            # instructions for a generic runtime, so this route never loads them.
            # It is not the sandbox fix: launching the resolved installation
            # binary is what lets Codex re-execute itself under the deny-root
            # profile below.
            "project_doc_max_bytes=0",
            'default_permissions="lar-reasoning"',
            'permissions.lar-reasoning.description="Isolated runtime reasoning"',
            'permissions.lar-reasoning.filesystem={":root"="deny",":minimal"="read",":workspace_roots"={"."="read"}}',
            "permissions.lar-reasoning.network.enabled=false",
        ):
            args.extend(("--config", setting))
        args.extend(
            (
                "--skip-git-repo-check",
                "--color",
                "never",
                "--cd",
                str(root),
                "--output-schema",
                str(schema),
            )
        )
        if self.profile.model != "default":
            args.extend(("--model", self.profile.model))
        if effort is not None:
            args.extend(("--config", f'model_reasoning_effort="{effort.value}"'))
        return [*args, "-"]


def _catalog_item(value: object) -> DiscoveredModel | None:
    if not isinstance(value, dict):
        return None
    model = value.get("model")
    display_name = value.get("displayName")
    efforts = value.get("supportedReasoningEfforts")
    hidden = value.get("hidden")
    if (
        not isinstance(model, str)
        or not model
        or model != model.strip()
        or len(model) > 256
        or model.startswith("-")
        or any(ord(char) < 32 for char in model)
        or not isinstance(display_name, str)
        or not display_name
        or display_name != display_name.strip()
        or len(display_name) > 256
        or any(ord(char) < 32 for char in display_name)
        or not isinstance(efforts, list)
        or type(hidden) is not bool
    ):
        return None
    supported: list[ReasoningEffort] = []
    for raw in efforts:
        if not isinstance(raw, dict) or not isinstance(raw.get("reasoningEffort"), str):
            return None
        try:
            effort = ReasoningEffort(raw["reasoningEffort"])
        except ValueError:
            # The runtime vocabulary intentionally omits provider-only values such
            # as Codex `ultra`; catalog display must not make them executable.
            continue
        if effort not in supported:
            supported.append(effort)
    raw_default = value.get("defaultReasoningEffort")
    try:
        default = ReasoningEffort(raw_default) if isinstance(raw_default, str) else None
    except ValueError:
        default = None
    if default not in supported:
        default = None
    return DiscoveredModel(
        model,
        display_name,
        ModelKind.REASONING,
        tuple(supported),
        default,
        None,
        True,
    )


def _response(stdout: str) -> object:
    for line in stdout.splitlines():
        try:
            frame = json.loads(line)
        except ValueError:
            continue
        if isinstance(frame, dict) and frame.get("id") == 2:
            return frame.get("result")
    return None
