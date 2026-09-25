"""Explicit approval and bounded file-change capabilities."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from bridge_agent.contracts.plugins import ServiceKey


@dataclass(frozen=True)
class ApprovalRequest:
    kind: Literal["write", "command"]
    operation_id: str
    workspace: Path
    target: str
    details: str


class ApprovalPolicy(Protocol):
    async def confirm(self, request: ApprovalRequest) -> bool: ...


@dataclass(frozen=True)
class ChangePreview:
    change_id: str
    path: str
    diff: str


@dataclass(frozen=True)
class ChangeResult:
    change_id: str
    path: str
    status: Literal["applied", "already_applied"]


class WorkspaceChanges(Protocol):
    async def preview(self, path: str, content: str) -> ChangePreview: ...

    async def apply(self, change_id: str) -> ChangeResult: ...


APPROVAL = ServiceKey[ApprovalPolicy]("actions.approval")
WORKSPACE_CHANGES = ServiceKey[WorkspaceChanges]("workspace.changes")


@dataclass(frozen=True)
class CommandResult:
    request_id: str
    command: str
    exit_code: int
    stdout: str
    stderr: str
    truncated: bool = False


class CommandRunner(Protocol):
    @property
    def names(self) -> tuple[str, ...]: ...

    async def run(self, command: str, request_id: str) -> CommandResult: ...

    async def cancel(self, request_id: str) -> bool: ...


COMMANDS = ServiceKey[CommandRunner]("workspace.commands")
