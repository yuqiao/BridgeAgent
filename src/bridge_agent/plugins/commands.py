"""Named local commands use argv and explicit workspace ownership."""

import asyncio
import os
import shlex
import signal
from collections.abc import Mapping
from pathlib import Path

from bridge_agent.contracts.actions import (
    ApprovalPolicy,
    ApprovalRequest,
    CommandResult,
)
from bridge_agent.contracts.errors import ActionError


class LocalCommandRunner:
    def __init__(
        self,
        root: Path,
        commands: Mapping[str, tuple[str, ...]],
        approval: ApprovalPolicy,
        *,
        timeout_seconds: float = 60,
    ) -> None:
        self._closed = False
        self._root = root.resolve()
        self._commands = dict(commands)
        self._approval = approval
        self._timeout = timeout_seconds
        self._results: dict[str, CommandResult] = {}
        self._running: dict[str, asyncio.Task[object]] = {}
        self._attempted: set[str] = set()

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._commands)

    async def run(self, command: str, request_id: str) -> CommandResult:
        if self._closed:
            raise ActionError("Command runner is closed")
        if request_id in self._running:
            raise ActionError("Command request is already running")
        task = asyncio.current_task()
        assert task is not None
        self._running[request_id] = task
        try:
            return await self._run(command, request_id)
        finally:
            self._running.pop(request_id, None)

    async def close(self) -> None:
        self._closed = True
        tasks = tuple(self._running.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def cancel(self, request_id: str) -> bool:
        task = self._running.get(request_id)
        if task is None:
            return False
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return True

    async def _run(self, command: str, request_id: str) -> CommandResult:
        if request_id in self._results:
            result = self._results[request_id]
            if result.command != command:
                raise ActionError("Request ID belongs to a different command")
            return result
        if request_id in self._attempted:
            raise ActionError(
                "Previous command did not finish; use a new request ID after checking its effects"
            )
        if command not in self._commands:
            raise ActionError("Unknown command template")
        if not await self._approval.confirm(
            ApprovalRequest(
                "command",
                request_id,
                self._root,
                command,
                shlex.join(self._commands[command]),
            )
        ):
            raise ActionError("Command denied")
        environment = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "VIRTUAL_ENV"}
        }
        self._attempted.add(request_id)
        try:
            process = await asyncio.create_subprocess_exec(
                *self._commands[command],
                cwd=self._root,
                env=environment,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except OSError:
            raise ActionError("Cannot start command") from None
        try:
            async with asyncio.timeout(self._timeout):
                stdout, stderr, truncated = await self._collect(process)
                await process.wait()
        except TimeoutError:
            raise ActionError("Command timed out") from None
        finally:
            await self._stop(process)
        assert process.returncode is not None
        result = CommandResult(
            request_id,
            command,
            process.returncode,
            stdout.decode(errors="replace"),
            stderr.decode(errors="replace"),
            truncated,
        )
        self._results[request_id] = result
        return result

    @staticmethod
    async def _stop(process: asyncio.subprocess.Process) -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        task = asyncio.create_task(process.wait())
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError

    @staticmethod
    async def _collect(
        process: asyncio.subprocess.Process,
    ) -> tuple[bytes, bytes, bool]:
        remaining = 65536
        truncated = False

        async def drain(stream: asyncio.StreamReader | None) -> bytes:
            nonlocal remaining, truncated
            assert stream is not None
            output = bytearray()
            while chunk := await stream.read(4096):
                accepted = chunk[:remaining]
                output.extend(accepted)
                remaining -= len(accepted)
                truncated |= len(accepted) < len(chunk)
            return bytes(output)

        stdout, stderr = await asyncio.gather(
            drain(process.stdout), drain(process.stderr)
        )
        return stdout, stderr, truncated
