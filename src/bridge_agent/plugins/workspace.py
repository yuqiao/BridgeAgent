"""Explicit workspace provider with validated, host-owned configuration."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from bridge_agent.contracts.errors import ConfigurationError
from bridge_agent.contracts.files import WORKSPACE_FILES
from bridge_agent.contracts.plugins import Plugin, PluginContext, PluginDefinition
from bridge_agent.plugins.filesystem import LocalWorkspaceFiles


class FilesConfig(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    max_entries: int = Field(default=10000, ge=1, le=100000)


@dataclass
class FilesPlugin:
    root: Path
    config: FilesConfig

    async def activate(self, context: PluginContext) -> None:
        context.provide(
            WORKSPACE_FILES,
            LocalWorkspaceFiles(self.root, max_entries=self.config.max_entries),
        )


def files_definition(root: Path | None) -> PluginDefinition:
    def prepare(config: Mapping[str, object]) -> Callable[[], Plugin]:
        if root is None:
            raise ConfigurationError("File provider requires an explicit workspace")
        parsed = FilesConfig.model_validate(dict(config))
        return lambda: FilesPlugin(root, parsed)

    return PluginDefinition("files.local", prepare, provides=(WORKSPACE_FILES,))
