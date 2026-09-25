"""Serialize approval prompts on the same reader as the CLI conversation."""

import asyncio
import sys

from bridge_agent.contracts.actions import ApprovalRequest


class ConsoleApproval:
    def __init__(self, reader: asyncio.StreamReader) -> None:
        self._reader = reader
        self._lock = asyncio.Lock()

    async def confirm(self, request: ApprovalRequest) -> bool:
        async with self._lock:
            print(
                f"Approve {request.kind}: {request.target}\nWorkspace: {request.workspace}\n"
                f"{request.details}\nAllow? [y/N] ",
                end="",
                file=sys.stderr,
                flush=True,
            )
            return (await self._reader.readline()).strip().lower() in {b"y", b"yes"}
