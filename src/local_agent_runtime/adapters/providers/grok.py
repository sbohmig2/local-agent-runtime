"""Grok CLI policy: private HOME, auth-only copy and disposable sessions."""

import uuid
from contextlib import suppress
from pathlib import Path

from local_agent_runtime.adapters.cli_base import CLIAdapterBase
from local_agent_runtime.adapters.cli_environment import _copy_grok_auth, _provider_environment
from local_agent_runtime.errors import RuntimeFailure


class GrokAdapter(CLIAdapterBase):
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

    def arguments(self, executable: str, root: Path, schema: Path) -> list[str]:
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
        return args
