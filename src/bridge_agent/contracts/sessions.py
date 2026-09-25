"""Optional runtime session inspection, without framework checkpoint objects."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from bridge_agent.contracts.plugins import ServiceKey


@dataclass(frozen=True)
class SessionInfo:
    session_id: str
    workspace: Path
    status: Literal["succeeded", "incomplete"]
    compatible: bool


class SessionControl(Protocol):
    async def get_session(self, session_id: str) -> SessionInfo | None: ...


SESSION_CONTROL = ServiceKey[SessionControl]("agent.sessions")
