"""Prove the wheel imports and serves authenticated health outside the source tree."""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PROBE = """
import os
import json
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
import local_agent_runtime as lar
from local_agent_runtime.api_contract import API_VERSION
from local_agent_runtime.providers import REASONING_PROVIDERS, EMBEDDING_PROVIDERS

assert len(REASONING_PROVIDERS) == 5
assert len(EMBEDDING_PROVIDERS) == 2
assert lar.RuntimeService.__module__ == "local_agent_runtime.service"
assert not Path(lar.__file__).resolve().is_relative_to(Path(sys.argv[3]).resolve())
cli = shutil.which("local-agent-runtime")
assert cli is not None
subprocess.run([cli, "--help"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
token = secrets.token_urlsafe(32)
with socket.socket() as listener:
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
process = subprocess.Popen(
    [sys.executable, "-m", "local_agent_runtime", "serve", "--config", sys.argv[1],
     "--state-root", sys.argv[2], "--port", str(port)],
    env={**os.environ, "LOCAL_AGENT_RUNTIME_TOKEN": token},
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
try:
    for attempt in range(100):
        try:
            request = urllib.request.Request(
                "http://127.0.0.1:" + str(port) + "/v1/health",
                headers={"Authorization": "Bearer " + token},
            )
            with urllib.request.urlopen(request, timeout=1) as response:
                assert response.status == 200
                assert json.load(response) == {
                    "status": "available",
                    "package_version": lar.__version__,
                    "api_version": API_VERSION,
                }
                print("Isolated wheel imports and authenticated gateway startup passed.")
                break
        except OSError:
            if process.poll() is not None:
                raise RuntimeError("Gateway exited") from None
            time.sleep(0.05)
    else:
        raise RuntimeError("Gateway startup timed out")
finally:
    process.terminate()
    process.wait(timeout=10)
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path)
    args = parser.parse_args()
    if args.wheel is None:
        wheels = list((ROOT / "dist").glob("local_agent_runtime-*-py3-none-any.whl"))
        if len(wheels) != 1:
            raise SystemExit("Expected exactly one built Local Agent Runtime wheel")
        wheel = wheels[0]
    else:
        wheel = args.wheel.resolve()
        if not wheel.is_file() or wheel.suffix != ".whl":
            raise SystemExit("The requested Local Agent Runtime wheel does not exist")
    with tempfile.TemporaryDirectory(prefix="lar-install-") as directory:
        subprocess.run(
            [
                "uv",
                "run",
                "--isolated",
                "--no-project",
                "--with",
                str(wheel),
                "python",
                "-c",
                PROBE,
                str(ROOT / "config/runtime.example.yaml"),
                str(Path(directory) / "state"),
                str(ROOT),
            ],
            cwd=directory,
            check=True,
            timeout=60,
        )
        consumer = Path(directory) / "typed_consumer.py"
        consumer.write_text(
            "from local_agent_runtime import RuntimeService\n"
            "service: type[RuntimeService] = RuntimeService\n",
            encoding="utf-8",
        )
        subprocess.run(
            [
                "uv",
                "run",
                "--isolated",
                "--no-project",
                "--with",
                str(wheel),
                "--with",
                f"mypy=={version('mypy')}",
                "mypy",
                "--strict",
                str(consumer),
            ],
            cwd=directory,
            check=True,
            timeout=60,
        )
        print("Strict external mypy consumer passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
