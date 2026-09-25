"""Framework-independent, bounded workspace file capabilities."""

from dataclasses import dataclass
from typing import Protocol

from bridge_agent.contracts.plugins import ServiceKey


@dataclass(frozen=True)
class FileLine:
    number: int
    text: str


@dataclass(frozen=True)
class FileRead:
    path: str
    lines: tuple[FileLine, ...]
    truncated: bool


@dataclass(frozen=True)
class FileList:
    paths: tuple[str, ...]
    truncated: bool


@dataclass(frozen=True)
class SearchHit:
    path: str
    line: int
    text: str


@dataclass(frozen=True)
class FileSearch:
    hits: tuple[SearchHit, ...]
    truncated: bool


class WorkspaceFiles(Protocol):
    async def list(self, path: str = ".", *, limit: int = 200) -> FileList: ...

    async def search(
        self, query: str, path: str = ".", *, limit: int = 200
    ) -> FileSearch: ...

    async def read(
        self, path: str, *, start_line: int = 1, limit: int = 200
    ) -> FileRead: ...


WORKSPACE_FILES = ServiceKey[WorkspaceFiles]("workspace.files")
