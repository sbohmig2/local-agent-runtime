"""Reject stale files or version drift in the packed TypeScript client."""

import json
import tomllib
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
CLIENT = REPOSITORY / "clients/typescript"
ROOT = CLIENT / "dist"
EXPECTED = {
    "generated.d.ts",
    "generated.js",
    "host/contracts.d.ts",
    "host/contracts.js",
    "host/errors.d.ts",
    "host/errors.js",
    "host/http-server.d.ts",
    "host/http-server.js",
    "host/index.d.ts",
    "host/index.js",
    "host/mcp-tools.d.ts",
    "host/mcp-tools.js",
    "host/runtime-supervisor.d.ts",
    "host/runtime-supervisor.js",
    "host/session-coordinator.d.ts",
    "host/session-coordinator.js",
    "index.d.ts",
    "index.js",
    "transport.d.ts",
    "transport.js",
}


def main() -> int:
    actual = {str(path.relative_to(ROOT)) for path in ROOT.rglob("*") if path.is_file()}
    if actual != EXPECTED:
        missing = sorted(EXPECTED - actual)
        unexpected = sorted(actual - EXPECTED)
        raise SystemExit(
            f"TypeScript package layout mismatch; missing={missing}, unexpected={unexpected}"
        )
    python_version = tomllib.loads((REPOSITORY / "pyproject.toml").read_text())["project"][
        "version"
    ]
    client_version = json.loads((CLIENT / "package.json").read_text())["version"]
    if client_version != python_version:
        raise SystemExit(
            f"Release version mismatch; python={python_version}, typescript={client_version}"
        )
    print("TypeScript package layout is clean and complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
