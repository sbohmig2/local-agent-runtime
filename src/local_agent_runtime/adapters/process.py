"""Bounded subprocess transport with process-group cleanup, including cancellation."""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from local_agent_runtime.errors import RuntimeFailure, provider_unavailable

MAX_PROCESS_BYTES = 1_000_000


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str


ProcessRunner = Callable[
    [Sequence[str], str | None, Path, Mapping[str, str], float], Awaitable[ProcessResult]
]


async def run_process(
    arguments: Sequence[str],
    stdin: str | None,
    cwd: Path,
    environment: Mapping[str, str],
    timeout: float,
) -> ProcessResult:
    if os.name != "posix":
        raise provider_unavailable("CLI process isolation requires a POSIX host")
    process: asyncio.subprocess.Process | None = None
    tasks: list[asyncio.Task[object]] = []

    async def read(stream: asyncio.StreamReader | None) -> bytes:
        assert stream is not None
        chunks = bytearray()
        while chunk := await stream.read(16_384):
            if len(chunks) + len(chunk) > MAX_PROCESS_BYTES:
                raise RuntimeFailure(
                    "output_limit_exceeded", "Provider process output exceeds its limit"
                )
            chunks.extend(chunk)
        return bytes(chunks)

    async def write() -> None:
        assert process is not None
        if process.stdin is not None:
            try:
                process.stdin.write((stdin or "").encode())
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                process.stdin.close()

    try:
        process = await asyncio.create_subprocess_exec(
            *arguments,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=dict(environment),
            start_new_session=True,
            limit=32_768,
        )
        async with asyncio.timeout(timeout):
            stdout_task = asyncio.create_task(read(process.stdout))
            stderr_task = asyncio.create_task(read(process.stderr))
            writer_task = asyncio.create_task(write())
            waiter_task = asyncio.create_task(process.wait())
            tasks.extend((stdout_task, stderr_task, writer_task, waiter_task))
            stdout, stderr, _, _ = await asyncio.gather(
                stdout_task, stderr_task, writer_task, waiter_task
            )
        return ProcessResult(
            process.returncode or 0,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )
    except TimeoutError:
        raise RuntimeFailure(
            "provider_timeout", "The provider process timed out", status_code=504
        ) from None
    except (OSError, ValueError):
        raise provider_unavailable("The provider process could not be started") from None
    finally:
        if process is not None:
            # Also stop descendants that outlive a successful parent. No orphan tool process.
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if process is not None:

            async def discard(stream: asyncio.StreamReader | None) -> None:
                if stream is not None:
                    while await stream.read(16_384):
                        pass

            # A paused full pipe can prevent Process.wait() from completing even
            # after SIGKILL. Drain without retaining bytes before waiting.
            await asyncio.gather(discard(process.stdout), discard(process.stderr), process.wait())
