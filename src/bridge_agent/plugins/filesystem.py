"""Local implementation of the workspace file capability."""

import asyncio
import os
from collections.abc import AsyncIterator
from itertools import islice
from pathlib import Path

from bridge_agent.contracts.errors import WorkspaceAccessError
from bridge_agent.contracts.files import (
    FileLine,
    FileList,
    FileRead,
    FileSearch,
    SearchHit,
)
from bridge_agent.plugins.file_policy import FilePolicy


class LocalWorkspaceFiles:
    def __init__(self, root: Path, *, max_entries: int = 10000) -> None:
        self._policy = FilePolicy(root)
        self.root = self._policy.root
        if type(max_entries) is not int or not 1 <= max_entries <= 100000:
            raise WorkspaceAccessError("Invalid traversal limit")
        self._max_entries = max_entries

    async def _walk(self, path: str) -> AsyncIterator[str | None]:
        root = self._policy.target(path)
        if not root.is_dir():
            raise WorkspaceAccessError("List/search require a directory")
        pending = [root]
        scanned = 0
        while pending:
            await asyncio.sleep(0)
            directory = pending.pop()
            with os.scandir(directory) as iterator:
                entries = sorted(
                    islice(iterator, self._max_entries - scanned + 1),
                    key=lambda item: item.name,
                )
            for entry in entries:
                await asyncio.sleep(0)
                scanned += 1
                if scanned > self._max_entries:
                    yield None
                    return
                relative = Path(entry.path).relative_to(self.root).as_posix()
                try:
                    target = self._policy.target(relative)
                    if target.is_dir():
                        if not entry.is_symlink():
                            pending.append(target)
                        continue
                    self._policy.text(target)
                except WorkspaceAccessError:
                    continue
                yield relative

    async def list(self, path: str = ".", *, limit: int = 200) -> FileList:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise WorkspaceAccessError("Invalid result limit")
        paths: list[str] = []
        truncated = False
        async for name in self._walk(path):
            if name is None or len(paths) >= limit:
                truncated = True
                break
            paths.append(name)
        paths.sort()
        return FileList(tuple(paths), truncated)

    async def search(
        self, query: str, path: str = ".", *, limit: int = 200
    ) -> FileSearch:
        if not query or len(query) > 1000:
            raise WorkspaceAccessError("Search query must contain 1–1000 characters")
        if type(limit) is not int or not 1 <= limit <= 200:
            raise WorkspaceAccessError("Invalid result limit")
        hits: list[SearchHit] = []
        remaining = 16000
        async for name in self._walk(path):
            if name is None:
                return FileSearch(tuple(hits), True)
            for number, line in enumerate(
                self._policy.text(self._policy.target(name)).splitlines(), 1
            ):
                if query in line:
                    if len(hits) >= limit:
                        return FileSearch(tuple(hits), True)
                    text = line[:remaining]
                    hits.append(SearchHit(name, number, text))
                    remaining -= len(text)
                    if remaining == 0:
                        return FileSearch(tuple(hits), True)
        return FileSearch(tuple(hits), False)

    async def read(
        self, path: str, *, start_line: int = 1, limit: int = 200, scope: str = "."
    ) -> FileRead:
        if (
            type(start_line) is not int
            or start_line < 1
            or type(limit) is not int
            or not 1 <= limit <= 200
        ):
            raise WorkspaceAccessError("Invalid read window")
        target = self._policy.target(path)
        if not target.is_relative_to(self._policy.target(scope)):
            raise WorkspaceAccessError("Path is outside the requested scope")
        lines = self._policy.text(target).splitlines()
        stop = start_line - 1 + limit
        output: list[FileLine] = []
        remaining = 16000
        truncated = stop < len(lines)
        for number, line in enumerate(lines[start_line - 1 : stop], start_line):
            if remaining == 0:
                truncated = True
                break
            output.append(FileLine(number, line[:remaining]))
            if len(line) > remaining:
                truncated = True
            remaining -= len(output[-1].text)
        return FileRead(
            path,
            tuple(output),
            truncated,
        )
