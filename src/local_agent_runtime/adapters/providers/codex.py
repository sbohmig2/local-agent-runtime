"""Codex CLI policy: ephemeral, deny-root sandbox, no native tools or instructions."""

from pathlib import Path

from local_agent_runtime.adapters.cli_base import CLIAdapterBase
from local_agent_runtime.adapters.cli_environment import _provider_environment


class CodexAdapter(CLIAdapterBase):
    def environment(self, root: Path) -> dict[str, str]:
        return _provider_environment("CODEX_HOME", "CODEX_CA_CERTIFICATE")

    def auth_arguments(self, executable: str) -> list[str]:
        return [executable, "login", "status"]

    def arguments(self, executable: str, root: Path, schema: Path) -> list[str]:
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
        return [*args, "-"]
