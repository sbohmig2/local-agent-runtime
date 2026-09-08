"""Claude CLI policy and native result wrapper; no provider branching elsewhere."""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar

from local_agent_runtime.adapters.cli_base import CLIAdapterBase
from local_agent_runtime.adapters.cli_environment import _provider_environment
from local_agent_runtime.adapters.process import ProcessResult
from local_agent_runtime.contracts import CompletionResult, ReasoningEffort
from local_agent_runtime.errors import provider_unavailable


class ClaudeAdapter(CLIAdapterBase):
    # The CLI's own level list excludes `minimal`, so no profile may declare it.
    TRANSPORT_EFFORTS: ClassVar[tuple[ReasoningEffort, ...]] = (
        ReasoningEffort.LOW,
        ReasoningEffort.MEDIUM,
        ReasoningEffort.HIGH,
        ReasoningEffort.XHIGH,
        ReasoningEffort.MAX,
    )
    VERIFIED_EFFORTS: ClassVar[Mapping[str, tuple[ReasoningEffort, ...]]] = {}

    def environment(self, root: Path) -> dict[str, str]:
        # The CLI resolves its stored login against the invoking account name, so a
        # bare environment reports an authenticated install as logged out.  USER is
        # an account label, never a credential.
        return _provider_environment("CLAUDE_CONFIG_DIR", "USER")

    def auth_arguments(self, executable: str) -> list[str]:
        return [executable, "auth", "status", "--json"]

    def authenticated(self, result: ProcessResult) -> bool | None:
        try:
            value = json.loads(result.stdout)
            if isinstance(value, dict) and type(value.get("loggedIn")) is bool:
                return result.returncode == 0 and value["loggedIn"]
        except ValueError:
            pass
        return None

    def arguments(
        self, executable: str, root: Path, schema: Path, effort: ReasoningEffort | None
    ) -> list[str]:
        args = [
            executable,
            "--print",
            "--safe-mode",
            "--no-session-persistence",
            "--disable-slash-commands",
            "--setting-sources",
            "",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--tools",
            "",
            "--permission-mode",
            "dontAsk",
            "--output-format",
            "json",
            "--json-schema",
            schema.read_text(encoding="utf-8"),
        ]
        if self.profile.model != "default":
            args.extend(("--model", self.profile.model))
        # The CLI warns and silently falls back on an unknown level, so the runtime
        # rejects unsupported efforts before dispatch rather than after.
        if effort is not None:
            args.extend(("--effort", effort.value))
        return args

    def decode(self, raw: str) -> CompletionResult:
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict) or payload.get("is_error"):
                raise ValueError
            output = payload.get("structured_output")
            if isinstance(output, dict):
                return super().decode(json.dumps(output))
            if isinstance(payload.get("result"), str):
                return super().decode(payload["result"])
        except ValueError:
            pass
        raise provider_unavailable("Claude returned an invalid result envelope")
