"""Skill metadata and on-demand content, independent of the Agent framework."""

from dataclasses import dataclass
from typing import Protocol

from bridge_agent.contracts.files import FileRead
from bridge_agent.contracts.plugins import ServiceKey


@dataclass(frozen=True)
class SkillSummary:
    name: str
    description: str
    path: str


@dataclass(frozen=True)
class LoadedSkill:
    summary: SkillSummary
    content: str


class SkillCatalog(Protocol):
    async def discover(self) -> tuple[SkillSummary, ...]: ...

    async def load(self, name: str) -> LoadedSkill: ...

    async def read_resource(
        self, name: str, path: str, *, start_line: int = 1, limit: int = 200
    ) -> FileRead: ...


SKILL_CATALOG = ServiceKey[SkillCatalog]("skills.catalog")
