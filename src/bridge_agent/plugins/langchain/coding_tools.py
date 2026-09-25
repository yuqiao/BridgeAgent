"""Model-facing changes and named commands with enforced approval."""

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict

from langchain_core.tools import BaseTool, ToolException, tool

from bridge_agent.contracts.actions import (
    COMMANDS,
    WORKSPACE_CHANGES,
    CommandRunner,
    WorkspaceChanges,
)
from bridge_agent.contracts.errors import ActionError, WorkspaceAccessError
from bridge_agent.contracts.files import WORKSPACE_FILES
from bridge_agent.contracts.plugins import Plugin, PluginContext, PluginDefinition
from bridge_agent.contracts.skills import SKILL_CATALOG
from bridge_agent.plugins.langchain.builtin import EmptyConfig
from bridge_agent.plugins.langchain.services import TOOLS
from bridge_agent.plugins.langchain.skill_tools import skill_tools
from bridge_agent.plugins.langchain.workspace_tools import workspace_tools


def coding_tools(
    changes: WorkspaceChanges, commands: CommandRunner
) -> tuple[BaseTool, ...]:
    async def invoke[T](operation: Awaitable[T]) -> T:
        try:
            return await operation
        except (ActionError, WorkspaceAccessError) as error:
            raise ToolException(str(error)) from None

    @tool
    async def preview_change(path: str, content: str) -> str:
        """Preview the complete replacement of a small text file. Does not write; returns an opaque change_id and diff."""
        return json.dumps(
            asdict(await invoke(changes.preview(path, content))), ensure_ascii=False
        )

    @tool
    async def apply_change(change_id: str) -> str:
        """Request approval and apply a previously previewed change. Stale previews are rejected."""
        return json.dumps(
            asdict(await invoke(changes.apply(change_id))), ensure_ascii=False
        )

    @tool
    async def run_command(command: str, request_id: str) -> str:
        """Request approval to run a configured command name, using a unique request_id. Arbitrary shell text is not accepted."""
        return json.dumps(
            asdict(await invoke(commands.run(command, request_id))), ensure_ascii=False
        )

    run_command.description += " Available names: " + json.dumps(commands.names)
    tools = (preview_change, apply_change, run_command)
    for item in tools:
        item.handle_tool_error = True
    return tools


class CodingToolsPlugin:
    def __init__(self, skills: bool = False) -> None:
        self._skills = skills

    async def activate(self, context: PluginContext) -> None:
        context.provide(
            TOOLS,
            (
                *(skill_tools(context.require(SKILL_CATALOG)) if self._skills else ()),
                *workspace_tools(context.require(WORKSPACE_FILES)),
                *coding_tools(
                    context.require(WORKSPACE_CHANGES), context.require(COMMANDS)
                ),
            ),
        )


def prepare(config: Mapping[str, object]) -> Callable[[], Plugin]:
    EmptyConfig.model_validate(dict(config))
    return CodingToolsPlugin


CODING_TOOLS = PluginDefinition(
    "tools.coding",
    prepare,
    requires=(WORKSPACE_FILES, WORKSPACE_CHANGES, COMMANDS),
    provides=(TOOLS,),
)


def prepare_skills(config: Mapping[str, object]) -> Callable[[], Plugin]:
    EmptyConfig.model_validate(dict(config))
    return lambda: CodingToolsPlugin(skills=True)


CODING_SKILLS = PluginDefinition(
    "tools.coding-skills",
    prepare_skills,
    requires=(WORKSPACE_FILES, WORKSPACE_CHANGES, COMMANDS, SKILL_CATALOG),
    provides=(TOOLS,),
)
