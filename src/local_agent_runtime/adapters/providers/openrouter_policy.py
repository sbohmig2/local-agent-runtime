"""OpenRouter authentication and explicit upstream policy for both capabilities."""

import re
from typing import Any

from local_agent_runtime.configuration import resolve_credential
from local_agent_runtime.contracts import ProviderConnection
from local_agent_runtime.errors import RuntimeFailure, invalid_configuration


def headers(connection: ProviderConnection) -> dict[str, str]:
    credential = resolve_credential(connection.credential_ref)
    if not credential or any(ord(char) < 33 or ord(char) > 126 for char in credential):
        raise RuntimeFailure(
            "credential_unavailable", "Provider credential is unavailable", status_code=503
        )
    return {"Content-Type": "application/json", "Authorization": f"Bearer {credential}"}


def routing(connection: ProviderConnection) -> dict[str, Any]:
    if not connection.upstream:
        raise invalid_configuration("OpenRouter requires an exact upstream provider")
    return {
        "only": [connection.upstream],
        "order": [connection.upstream],
        "allow_fallbacks": False,
        "require_parameters": True,
        "data_collection": "deny",
    }


def upstream_matches(configured: str | None, effective: str | None) -> bool:
    if effective is None:
        return True

    def normalize(value: str) -> str:
        return re.sub(r"[^a-z0-9]", "", value.casefold())

    return configured is not None and normalize(configured) == normalize(effective)
