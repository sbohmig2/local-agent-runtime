"""Bounded subprocess transport with process-group cleanup, including cancellation."""

from __future__ import annotations

import asyncio
import json
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

JsonRpcProcessRunner = Callable[
    [Sequence[str], Sequence[tuple[int, str]], Path, Mapping[str, str], float],
    Awaitable[ProcessResult],
]


async def run_json_rpc_process(
    arguments: Sequence[str],
    requests: Sequence[tuple[int, str]],
    cwd: Path,
    environment: Mapping[str, str],
    timeout: float,
) -> ProcessResult:
    """Run a bounded line-delimited JSON-RPC handshake.

    Requests are deliberately serialized: several app servers do not accept a
    post-initialize request until the initialize response has been emitted.
    """
    if os.name != "posix":
        raise provider_unavailable("CLI process isolation requires a POSIX host")
    process: asyncio.subprocess.Process | None = None
    stderr_task: asyncio.Task[bytes] | None = None
    output = bytearray()

    async def read_stderr() -> bytes:
        assert process is not None and process.stderr is not None
        chunks = bytearray()
        while chunk := await process.stderr.read(16_384):
            if len(chunks) + len(chunk) > MAX_PROCESS_BYTES:
                raise RuntimeFailure(
                    "output_limit_exceeded", "Provider process output exceeds its limit"
                )
            chunks.extend(chunk)
        return bytes(chunks)

    try:
        process = await asyncio.create_subprocess_exec(
            *arguments,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=dict(environment),
            start_new_session=True,
            limit=32_768,
        )
        stderr_task = asyncio.create_task(read_stderr())
        async with asyncio.timeout(timeout):
            assert process.stdin is not None and process.stdout is not None
            for expected_id, request in requests:
                process.stdin.write((request + "\n").encode())
                await process.stdin.drain()
                matched = False
                while line := await process.stdout.readline():
                    if len(output) + len(line) > MAX_PROCESS_BYTES:
                        raise RuntimeFailure(
                            "output_limit_exceeded", "Provider process output exceeds its limit"
                        )
                    output.extend(line)
                    try:
                        frame = json.loads(line)
                    except (UnicodeError, ValueError):
                        continue
                    if isinstance(frame, dict) and frame.get("id") == expected_id:
                        matched = True
                        break
                if not matched:
                    raise provider_unavailable("The provider ended its catalog handshake")
            process.stdin.close()
            while chunk := await process.stdout.read(16_384):
                if len(output) + len(chunk) > MAX_PROCESS_BYTES:
                    raise RuntimeFailure(
                        "output_limit_exceeded", "Provider process output exceeds its limit"
                    )
                output.extend(chunk)
            await process.wait()
            stderr = await stderr_task
        return ProcessResult(
            process.returncode or 0,
            output.decode("utf-8", errors="replace"),
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
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        if stderr_task is not None and not stderr_task.done():
            stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)
        if process is not None:
            await process.wait()


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
