"""Skill tools extend the read-only tool set without changing the Agent loop."""

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict

from langchain_core.tools import BaseTool, ToolException, tool

from bridge_agent.contracts.errors import SkillError, WorkspaceAccessError
from bridge_agent.contracts.files import WORKSPACE_FILES
from bridge_agent.contracts.plugins import Plugin, PluginContext, PluginDefinition
from bridge_agent.contracts.skills import SKILL_CATALOG, SkillCatalog
from bridge_agent.plugins.langchain.builtin import EmptyConfig
from bridge_agent.plugins.langchain.services import TOOLS
from bridge_agent.plugins.langchain.workspace_tools import workspace_tools


def skill_tools(catalog: SkillCatalog) -> tuple[BaseTool, ...]:
    async def invoke[T](operation: Awaitable[T]) -> T:
        try:
            return await operation
        except (SkillError, WorkspaceAccessError) as error:
            raise ToolException(str(error)) from None

    @tool
    async def list_skills() -> str:
        """Discover available Skill names and descriptions without loading instructions."""
        return json.dumps(
            [asdict(item) for item in await invoke(catalog.discover())],
            ensure_ascii=False,
        )

    @tool
    async def load_skill(name: str) -> str:
        """Load a named Skill's instructions before following its task workflow."""
        return json.dumps(asdict(await invoke(catalog.load(name))), ensure_ascii=False)

    @tool
    async def read_skill_resource(
        name: str, path: str, start_line: int = 1, limit: int = 200
    ) -> str:
        """Read a reference file relative to the named Skill directory. Does not execute scripts."""
        return json.dumps(
            asdict(
                await invoke(
                    catalog.read_resource(
                        name, path, start_line=start_line, limit=limit
                    )
                )
            ),
            ensure_ascii=False,
        )

    tools = (list_skills, load_skill, read_skill_resource)
    for item in tools:
        item.handle_tool_error = True
    return tools


class SkillToolsPlugin:
    async def activate(self, context: PluginContext) -> None:
        context.provide(
            TOOLS,
            (
                *workspace_tools(context.require(WORKSPACE_FILES)),
                *skill_tools(context.require(SKILL_CATALOG)),
            ),
        )


def prepare(config: Mapping[str, object]) -> Callable[[], Plugin]:
    EmptyConfig.model_validate(dict(config))
    return SkillToolsPlugin


SKILL_TOOLS = PluginDefinition(
    "tools.skills",
    prepare,
    requires=(WORKSPACE_FILES, SKILL_CATALOG),
    provides=(TOOLS,),
)
