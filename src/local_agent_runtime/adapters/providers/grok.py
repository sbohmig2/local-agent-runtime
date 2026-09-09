"""Grok CLI policy: private HOME, auth-only copy, disposable sessions, native envelope."""

import json
import uuid
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import ClassVar

from local_agent_runtime.adapters.cli_base import CLIAdapterBase
from local_agent_runtime.adapters.cli_environment import _copy_grok_auth, _provider_environment
from local_agent_runtime.adapters.http_transport import response_identity, safe_usage
from local_agent_runtime.contracts import (
    Capabilities,
    CompletionResult,
    DiscoveredModel,
    ModelDiscovery,
    ModelKind,
    ReasoningEffort,
)
from local_agent_runtime.errors import RuntimeFailure, provider_unavailable

# `max_turns`, `length` and `interrupted` mean the turn stopped early. Reporting
# them as completion would be misleading even when structuredOutput parses.
ACCEPTED_STOP_REASONS = frozenset({"end_turn", "stop", "tool_use"})


class GrokAdapter(CLIAdapterBase):
    # The CLI rejects any level outside this list before a model is reached.
    TRANSPORT_EFFORTS: ClassVar[tuple[ReasoningEffort, ...]] = (
        ReasoningEffort.LOW,
        ReasoningEffort.MEDIUM,
        ReasoningEffort.HIGH,
        ReasoningEffort.XHIGH,
    )
    VERIFIED_EFFORTS: ClassVar[Mapping[str, tuple[ReasoningEffort, ...]]] = {}
    CATALOG: ClassVar[tuple[DiscoveredModel, ...]] = (
        DiscoveredModel("grok-4.6", "Grok 4.6", ModelKind.REASONING),
        DiscoveredModel("grok-4.5", "Grok 4.5", ModelKind.REASONING),
    )

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(
            model_discovery=True,
            reasoning_effort_control=bool(self.reasoning_efforts),
        )

    async def discover_models(self) -> ModelDiscovery:
        return ModelDiscovery(
            supported=True,
            models=tuple(item.model for item in self.CATALOG),
            detail_code="maintained_catalog",
            details=self.CATALOG,
        )

    def environment(self, root: Path) -> dict[str, str]:
        environment = _provider_environment("XAI_API_KEY")
        source = environment.get("HOME")
        if source:
            _copy_grok_auth(Path(source), root)
        environment.update({"HOME": str(root), "XDG_CONFIG_HOME": str(root), "TMPDIR": str(root)})
        return environment

    def auth_arguments(self, executable: str) -> None:
        # `grok models` is not an authentication guarantee; don't infer readiness.
        return None

    async def cleanup(self, arguments: list[str], root: Path, environment: dict[str, str]) -> None:
        session_id = arguments[arguments.index("--session-id") + 1]
        with suppress(RuntimeFailure):
            await self.runner(
                [arguments[0], "sessions", "delete", session_id], None, root, environment, 15
            )

    def input(self, prompt: str, root: Path) -> None:
        path = root / "prompt.txt"
        path.write_text(prompt, encoding="utf-8")
        path.chmod(0o600)

    def arguments(
        self, executable: str, root: Path, schema: Path, effort: ReasoningEffort | None
    ) -> list[str]:
        args = [
            executable,
            "--prompt-file",
            str(root / "prompt.txt"),
            "--permission-mode",
            "dontAsk",
            "--tools",
            "",
            "--disable-web-search",
            "--no-subagents",
            "--no-plan",
            "--verbatim",
            "--cwd",
            str(root),
            "--session-id",
            str(uuid.uuid4()),
            "--output-format",
            "json",
            "--json-schema",
            schema.read_text(encoding="utf-8"),
        ]
        if self.profile.model != "default":
            args.extend(("--model", self.profile.model))
        if effort is not None:
            args.extend(("--reasoning-effort", effort.value))
        return args

    def decode(self, raw: str) -> CompletionResult:
        """Unwrap Grok's native JSON result.

        `--output-format json` returns a session envelope, not the bare structured
        object.  `structuredOutput` carries the schema-constrained value; `text`
        carries the same payload as a JSON string when the wrapper omits it.
        A refused, errored or early-stopped turn is refused here rather than
        reported as a completion just because its payload happens to parse.
        """
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError
            if any(payload.get(name) for name in ("is_error", "isError", "error")):
                raise RuntimeFailure(
                    "provider_failure", "Grok reported a failed turn", status_code=502
                )
            stop = payload.get("stopReason")
            if stop is not None and (
                not isinstance(stop, str) or stop not in ACCEPTED_STOP_REASONS
            ):
                raise RuntimeFailure(
                    "provider_incomplete",
                    "Grok stopped before producing a complete result",
                    status_code=502,
                )
            output = payload.get("structuredOutput")
            decoded = (
                super().decode(json.dumps(output))
                if isinstance(output, dict)
                else super().decode(payload["text"])
                if isinstance(payload.get("text"), str)
                else None
            )
            if decoded is not None:
                return replace(
                    decoded,
                    effective_model=_effective_model(payload.get("modelUsage")),
                    usage=safe_usage(payload.get("usage")),
                )
        except ValueError:
            pass
        raise provider_unavailable("Grok returned an invalid result envelope")


def _effective_model(usage: object) -> str | None:
    """Only an unambiguous single-model record establishes the effective model."""
    if not isinstance(usage, Mapping) or len(usage) != 1:
        return None
    return response_identity(next(iter(usage)))
