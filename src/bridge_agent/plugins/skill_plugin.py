"""Explicit filesystem Skill provider, mounted through the normal host."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, field_validator

from bridge_agent.contracts.files import WORKSPACE_FILES
from bridge_agent.contracts.plugins import Plugin, PluginContext, PluginDefinition
from bridge_agent.contracts.skills import SKILL_CATALOG
from bridge_agent.plugins.skills import FilesystemSkillCatalog


class SkillsConfig(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    roots: list[str] = Field(min_length=1, max_length=16)

    @field_validator("roots")
    @classmethod
    def valid_roots(cls, roots: list[str]) -> list[str]:
        normalized = [PurePosixPath(root).as_posix() for root in roots]
        if len(set(normalized)) != len(roots) or any(
            not root
            or "\x00" in root
            or PurePosixPath(root).is_absolute()
            or ".." in PurePosixPath(root).parts
            for root in roots
        ):
            raise ValueError(
                "Skill roots must be unique workspace-relative directories"
            )
        return normalized


@dataclass
class SkillsPlugin:
    config: SkillsConfig

    async def activate(self, context: PluginContext) -> None:
        context.provide(
            SKILL_CATALOG,
            FilesystemSkillCatalog(
                context.require(WORKSPACE_FILES), tuple(self.config.roots)
            ),
        )


def prepare(config: Mapping[str, object]) -> Callable[[], Plugin]:
    parsed = SkillsConfig.model_validate(dict(config))
    return lambda: SkillsPlugin(parsed)


SKILLS = PluginDefinition(
    "skills.filesystem", prepare, requires=(WORKSPACE_FILES,), provides=(SKILL_CATALOG,)
)
