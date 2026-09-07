"""Run the deterministic local and CI quality gate."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def run(*command: str) -> None:
    print(f"+ {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=REPO_ROOT, check=True)


def main() -> int:
    python = sys.executable
    run("ruff", "format", "--check", ".")
    run("ruff", "check", ".")
    run("mypy")
    run("pytest")
    run(python, "scripts/generate_contracts.py", "--check")
    run("npm", "--prefix", "clients/typescript", "test")
    run(python, "scripts/check_typescript_package.py")
    run(python, "scripts/validate_docs.py")
    run(python, "scripts/build_release.py", "--allow-dirty")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
