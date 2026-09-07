"""Command-line entry point for the optional loopback gateway."""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

import uvicorn

from local_agent_runtime.bootstrap import build_runtime
from local_agent_runtime.gateway import create_app


def _loopback_host(value: str) -> str:
    if value not in {"127.0.0.1", "::1", "localhost"}:
        raise argparse.ArgumentTypeError("The gateway host must be loopback")
    return value


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("The gateway port must be an integer") from None
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("The gateway port is out of bounds")
    return port


def _environment_name(value: str) -> str:
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", value):
        raise argparse.ArgumentTypeError("The token environment variable name is invalid")
    return value


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="local-agent-runtime")
    commands = root.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--config", type=Path, required=True)
    serve.add_argument("--state-root", type=Path, required=True)
    serve.add_argument("--token-env", type=_environment_name, default="LOCAL_AGENT_RUNTIME_TOKEN")
    serve.add_argument("--host", type=_loopback_host, default="127.0.0.1")
    serve.add_argument("--port", type=_port, default=8765)
    serve.add_argument("--origin", action="append", default=[])
    return root


def main() -> int:
    args = parser().parse_args()
    if args.command == "serve":
        token = os.environ.get(args.token_env)
        if token is None:
            parser().error(f"Environment variable {args.token_env} is required")
        runtime = build_runtime(args.config, args.state_root)
        app = create_app(
            runtime,
            bearer_token=token,
            allowed_origins=frozenset(args.origin),
        )
        uvicorn.run(app, host=args.host, port=args.port, log_config=None, access_log=False)
        return 0
    raise AssertionError("unreachable")
