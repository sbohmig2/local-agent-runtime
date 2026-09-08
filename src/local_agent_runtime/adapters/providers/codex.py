"""Codex CLI policy: ephemeral, deny-root sandbox, no native tools or instructions."""

from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar

from local_agent_runtime.adapters.cli_base import CLIAdapterBase
from local_agent_runtime.adapters.cli_environment import _provider_environment
from local_agent_runtime.contracts import ReasoningEffort


class CodexAdapter(CLIAdapterBase):
    TRANSPORT_EFFORTS: ClassVar[tuple[ReasoningEffort, ...]] = tuple(ReasoningEffort)
    # `codex exec` forwards an unrecognized level without complaint and each model
    # advertises its own supported levels, so no model is pre-qualified here.
    VERIFIED_EFFORTS: ClassVar[Mapping[str, tuple[ReasoningEffort, ...]]] = {}

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
