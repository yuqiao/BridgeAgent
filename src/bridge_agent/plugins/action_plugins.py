"""Explicit approval, local edits, and named command providers."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from bridge_agent.contracts.actions import (
    APPROVAL,
    COMMANDS,
    WORKSPACE_CHANGES,
    ApprovalPolicy,
    ApprovalRequest,
)
from bridge_agent.contracts.errors import ConfigurationError
from bridge_agent.contracts.files import WORKSPACE_FILES
from bridge_agent.contracts.plugins import Plugin, PluginContext, PluginDefinition
from bridge_agent.plugins.changes import LocalWorkspaceChanges
from bridge_agent.plugins.commands import LocalCommandRunner
from bridge_agent.plugins.filesystem import LocalWorkspaceFiles
from bridge_agent.plugins.workspace import FilesConfig


class ApprovalConfig(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    write_paths: list[str] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)


class CommandsConfig(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    commands: dict[str, list[str]]
    timeout_seconds: float = Field(default=60, gt=0, le=600)

    @field_validator("commands")
    @classmethod
    def validate_commands(cls, commands: dict[str, list[str]]) -> dict[str, list[str]]:
        if len(commands) > 100 or any(
            not name
            or len(name) > 100
            or not argv
            or not argv[0]
            or len(argv) > 100
            or any("\x00" in arg or len(arg) > 16000 for arg in argv)
            for name, argv in commands.items()
        ):
            raise ValueError("Invalid command templates")
        return commands


@dataclass
class ConfiguredApproval:
    config: ApprovalConfig
    interactive: ApprovalPolicy | None

    async def confirm(self, request: ApprovalRequest) -> bool:
        allowed = (
            self.config.write_paths if request.kind == "write" else self.config.commands
        )
        if request.target in allowed:
            return True
        return (
            await self.interactive.confirm(request)
            if self.interactive is not None
            else False
        )


@dataclass
class ApprovalPlugin:
    policy: ApprovalPolicy

    async def activate(self, context: PluginContext) -> None:
        context.provide(APPROVAL, self.policy)


@dataclass
class EditableFilesPlugin:
    root: Path
    config: FilesConfig

    async def activate(self, context: PluginContext) -> None:
        context.provide(
            WORKSPACE_FILES,
            LocalWorkspaceFiles(self.root, max_entries=self.config.max_entries),
        )
        context.provide(
            WORKSPACE_CHANGES,
            LocalWorkspaceChanges(self.root, context.require(APPROVAL)),
        )


@dataclass
class CommandsPlugin:
    root: Path
    config: CommandsConfig

    async def activate(self, context: PluginContext) -> None:
        runner = LocalCommandRunner(
            self.root,
            {name: tuple(argv) for name, argv in self.config.commands.items()},
            context.require(APPROVAL),
            timeout_seconds=self.config.timeout_seconds,
        )
        context.on_close_async(runner.close)
        context.provide(COMMANDS, runner)


def action_definitions(
    root: Path | None, interactive: ApprovalPolicy | None
) -> tuple[PluginDefinition, ...]:
    def prepare_approval(config: Mapping[str, object]) -> Callable[[], Plugin]:
        parsed = ApprovalConfig.model_validate(dict(config))
        return lambda: ApprovalPlugin(ConfiguredApproval(parsed, interactive))

    def prepare_files(config: Mapping[str, object]) -> Callable[[], Plugin]:
        if root is None:
            raise ConfigurationError("Editable files require an explicit workspace")
        parsed = FilesConfig.model_validate(dict(config))
        return lambda: EditableFilesPlugin(root, parsed)

    def prepare_commands(config: Mapping[str, object]) -> Callable[[], Plugin]:
        if root is None:
            raise ConfigurationError("Commands require an explicit workspace")
        parsed = CommandsConfig.model_validate(dict(config))
        return lambda: CommandsPlugin(root, parsed)

    return (
        PluginDefinition("approval.policy", prepare_approval, provides=(APPROVAL,)),
        PluginDefinition(
            "files.editable",
            prepare_files,
            requires=(APPROVAL,),
            provides=(WORKSPACE_FILES, WORKSPACE_CHANGES),
        ),
        PluginDefinition(
            "commands.local",
            prepare_commands,
            requires=(APPROVAL,),
            provides=(COMMANDS,),
        ),
    )
