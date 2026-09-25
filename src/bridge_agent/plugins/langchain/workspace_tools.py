"""Read-only model tools over the replaceable workspace capability."""

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict

from langchain_core.tools import BaseTool, ToolException, tool

from bridge_agent.contracts.errors import WorkspaceAccessError
from bridge_agent.contracts.files import WORKSPACE_FILES, WorkspaceFiles
from bridge_agent.contracts.plugins import Plugin, PluginContext, PluginDefinition
from bridge_agent.plugins.langchain.builtin import EmptyConfig
from bridge_agent.plugins.langchain.services import TOOLS


def workspace_tools(files: WorkspaceFiles) -> tuple[BaseTool, ...]:
    async def invoke[T](operation: Awaitable[T]) -> T:
        try:
            return await operation
        except WorkspaceAccessError as error:
            raise ToolException(str(error)) from None

    @tool
    async def list_files(path: str = ".", limit: int = 200) -> str:
        """List allowed text files under a workspace-relative directory."""
        return json.dumps(
            asdict(await invoke(files.list(path, limit=limit))), ensure_ascii=False
        )

    @tool
    async def read_file(path: str, start_line: int = 1, limit: int = 200) -> str:
        """Read a UTF-8 workspace file with 1-based line numbers. Cite these paths and lines."""
        return json.dumps(
            asdict(await invoke(files.read(path, start_line=start_line, limit=limit))),
            ensure_ascii=False,
        )

    @tool
    async def search_files(query: str, path: str = ".", limit: int = 200) -> str:
        """Find literal text in allowed workspace files, returning paths and line numbers."""
        return json.dumps(
            asdict(await invoke(files.search(query, path, limit=limit))),
            ensure_ascii=False,
        )

    tools = (list_files, read_file, search_files)
    for item in tools:
        item.handle_tool_error = True
    return tools


class WorkspaceToolsPlugin:
    async def activate(self, context: PluginContext) -> None:
        context.provide(TOOLS, workspace_tools(context.require(WORKSPACE_FILES)))


def prepare(config: Mapping[str, object]) -> Callable[[], Plugin]:
    EmptyConfig.model_validate(dict(config))
    return WorkspaceToolsPlugin


WORKSPACE_TOOLS = PluginDefinition(
    "tools.workspace", prepare, requires=(WORKSPACE_FILES,), provides=(TOOLS,)
)
