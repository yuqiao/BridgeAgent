"""Framework-independent requests and results for replaceable Agent runtimes."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from bridge_agent.contracts.plugins import ServiceKey


@dataclass(frozen=True)
class RunRequest:
    session_id: str
    text: str
    workspace: Path


@dataclass(frozen=True)
class ToolResult:
    name: str
    call_id: str
    content: str
    status: Literal["success", "error"] = "success"


@dataclass(frozen=True)
class RunResult:
    text: str
    tools: tuple[ToolResult, ...] = ()


class AgentRuntime(Protocol):
    async def run(self, request: RunRequest) -> RunResult: ...


AGENT_RUNTIME = ServiceKey[AgentRuntime]("agent.runtime")
