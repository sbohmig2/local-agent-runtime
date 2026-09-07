"""Claude CLI policy and native result wrapper; no provider branching elsewhere."""

import json
from pathlib import Path

from local_agent_runtime.adapters.cli_base import CLIAdapterBase
from local_agent_runtime.adapters.cli_environment import _provider_environment
from local_agent_runtime.adapters.process import ProcessResult
from local_agent_runtime.contracts import CompletionResult
from local_agent_runtime.errors import provider_unavailable


class ClaudeAdapter(CLIAdapterBase):
    def environment(self, root: Path) -> dict[str, str]:
        return _provider_environment("CLAUDE_CONFIG_DIR")

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

    def arguments(self, executable: str, root: Path, schema: Path) -> list[str]:
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
