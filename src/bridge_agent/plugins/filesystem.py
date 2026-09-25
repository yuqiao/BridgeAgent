"""Local implementation of the workspace file capability."""

import asyncio
import os
from collections.abc import AsyncIterator
from itertools import islice
from pathlib import Path

from pathspec import GitIgnoreSpec

from bridge_agent.contracts.errors import ConfigurationError, WorkspaceAccessError
from bridge_agent.contracts.files import (
    FileLine,
    FileList,
    FileRead,
    FileSearch,
    SearchHit,
)


class LocalWorkspaceFiles:
    def __init__(self, root: Path, *, max_entries: int = 10000) -> None:
        self.root = root.resolve()
        if not self.root.is_dir():
            raise WorkspaceAccessError("Workspace must be an existing directory")
        if type(max_entries) is not int or not 1 <= max_entries <= 100000:
            raise WorkspaceAccessError("Invalid traversal limit")
        self._max_entries = max_entries

    def _ignored(self, relative: Path) -> bool:
        rules: list[tuple[Path, GitIgnoreSpec]] = []
        parent = self.root
        for part in relative.parts:
            ignore = parent / ".gitignore"
            if ignore.is_file():
                if ignore.is_symlink() or ignore.stat().st_size > 65536:
                    raise ConfigurationError("Invalid ignore file")
                try:
                    with ignore.open("rb") as stream:
                        data = stream.read(65537)
                    if len(data) > 65536:
                        raise ConfigurationError("Invalid ignore file")
                    spec = GitIgnoreSpec.from_lines(data.decode("utf-8").splitlines())
                except (OSError, UnicodeError, ValueError):
                    raise ConfigurationError("Invalid ignore file") from None
                rules.append((parent, spec))
            candidate = parent / part
            ignored = False
            for base, spec in rules:
                name = candidate.relative_to(base).as_posix()
                if candidate.is_dir():
                    name += "/"
                match = spec.check_file(name)
                if match.include is not None:
                    ignored = match.include
            if ignored:
                return True
            parent = candidate
        return False

    def _target(self, path: str) -> Path:
        if (
            not path
            or Path(path).is_absolute()
            or ".." in Path(path).parts
            or "\x00" in path
        ):
            raise WorkspaceAccessError("Expected a relative workspace path")
        target = (self.root / path).resolve()
        if not target.is_relative_to(self.root):
            raise WorkspaceAccessError("Path is outside workspace")
        for candidate in (Path(path), target.relative_to(self.root)):
            if any(
                part.lower()
                in {".git", ".ssh", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}
                or part.lower().startswith(".env")
                or part.lower().endswith((".pem", ".key", ".p12", ".pfx"))
                for part in candidate.parts
            ):
                raise WorkspaceAccessError("Path is protected")
            if self._ignored(candidate):
                raise WorkspaceAccessError("Path is ignored")
        return target

    def _text(self, target: Path) -> str:
        if not target.is_file():
            raise WorkspaceAccessError("Path must be an existing regular file")
        if target.stat().st_size > 1024 * 1024:
            raise WorkspaceAccessError("File exceeds size limit")
        try:
            with target.open("rb") as stream:
                data = stream.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                raise WorkspaceAccessError("File exceeds size limit")
            text = data.decode("utf-8")
        except UnicodeError:
            raise WorkspaceAccessError("File must contain UTF-8 text") from None
        if "\x00" in text:
            raise WorkspaceAccessError("File must contain UTF-8 text")
        return text

    async def _walk(self, path: str) -> AsyncIterator[str | None]:
        root = self._target(path)
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
                    target = self._target(relative)
                    if target.is_dir():
                        if not entry.is_symlink():
                            pending.append(target)
                        continue
                    self._text(target)
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
                self._text(self._target(name)).splitlines(), 1
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
        self, path: str, *, start_line: int = 1, limit: int = 200
    ) -> FileRead:
        if (
            type(start_line) is not int
            or start_line < 1
            or type(limit) is not int
            or not 1 <= limit <= 200
        ):
            raise WorkspaceAccessError("Invalid read window")
        lines = self._text(self._target(path)).splitlines()
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
