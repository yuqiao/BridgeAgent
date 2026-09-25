"""File tools composed through the real YAML catalog and plugin host."""

import asyncio
import json
from pathlib import Path

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from bridge_agent.application.agent import AgentSession
from bridge_agent.bootstrap.agent import agent_catalog
from bridge_agent.contracts.agent import AGENT_RUNTIME
from bridge_agent.contracts.errors import AgentInputError, ConfigurationError
from bridge_agent.contracts.files import (
    WORKSPACE_FILES,
    FileLine,
    FileList,
    FileRead,
    FileSearch,
    SearchHit,
)
from bridge_agent.contracts.plugins import PluginDefinition
from bridge_agent.kernel.host import PluginHost
from bridge_agent.plugins.langchain.services import MODEL, TOOLS


class RepositoryModel(BaseChatModel):
    @property
    def _llm_type(self):
        return "repository-test"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        last = messages[-1]
        if isinstance(last, ToolMessage) and last.name == "read_file":
            data = json.loads(last.content)
            answer = AIMessage(content=f"{data['path']}:2: {data['lines'][1]['text']}")
        elif isinstance(last, ToolMessage):
            answer = AIMessage(
                content="",
                tool_calls=[
                    {"id": "read", "name": "read_file", "args": {"path": "code.py"}}
                ],
            )
        else:
            answer = AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "search",
                        "name": "search_files",
                        "args": {"query": "answer"},
                    }
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=answer)])


class ModelPlugin:
    async def activate(self, context):
        context.provide(MODEL, RepositoryModel())


TEST_MODEL = PluginDefinition(
    "model.test", lambda config: ModelPlugin, provides=(MODEL,)
)


def test_file_provider_requires_an_explicit_workspace(tmp_path: Path) -> None:
    config = tmp_path / "files.yaml"
    config.write_text("version: 1\nplugins: [{name: files.local}]\n")
    with pytest.raises(ConfigurationError):
        agent_catalog(environment={}).load(config)


def agent_config(tmp_path: Path) -> Path:
    path = tmp_path / "agent.yaml"
    path.write_text("""version: 1
plugins:
  - name: runtime.langchain
  - name: model.test
  - name: checkpoint.memory
  - name: files.local
  - name: tools.workspace
""")
    return path


def test_yaml_assembles_read_only_tools_for_the_selected_workspace(
    tmp_path: Path,
) -> None:
    (tmp_path / "code.py").write_text("def answer():\n    return 42\n")
    config = tmp_path / "agent.yaml"
    config.write_text("""version: 1
plugins:
  - name: tools.workspace
  - name: files.local
""")

    async def scenario() -> None:
        catalog = agent_catalog(workspace=tmp_path, environment={})
        async with PluginHost(catalog.load(config)) as host:
            tools = {tool.name: tool for tool in host.resolve(TOOLS)}
            assert set(tools) == {"list_files", "read_file", "search_files"}
            result = json.loads(await tools["read_file"].ainvoke({"path": "code.py"}))
            assert result["path"] == "code.py"
            assert result["lines"][1] == {"number": 2, "text": "    return 42"}

    asyncio.run(scenario())


def test_runtime_rejects_a_workspace_different_from_its_file_provider(
    tmp_path: Path,
) -> None:
    config = agent_config(tmp_path)
    other = tmp_path / "other"
    other.mkdir()

    async def scenario() -> None:
        catalog = agent_catalog((TEST_MODEL,), workspace=tmp_path, environment={})
        async with PluginHost(catalog.load(config)) as host:
            with pytest.raises(AgentInputError, match="workspace"):
                await AgentSession(host.resolve(AGENT_RUNTIME), other).ask(
                    "Find answer"
                )

    asyncio.run(scenario())


def test_agent_searches_reads_and_cites_actual_workspace_lines(tmp_path: Path) -> None:
    (tmp_path / "code.py").write_text("def answer():\n    return 42\n")
    config = agent_config(tmp_path)

    async def scenario() -> None:
        catalog = agent_catalog((TEST_MODEL,), workspace=tmp_path, environment={})
        async with PluginHost(catalog.load(config)) as host:
            result = await AgentSession(host.resolve(AGENT_RUNTIME), tmp_path).ask(
                "Find answer"
            )
            assert result.text == "code.py:2:     return 42"
            assert [item.name for item in result.tools] == ["search_files", "read_file"]
            assert json.loads(result.tools[0].content)["hits"][0]["line"] == 1

    asyncio.run(scenario())


def test_model_receives_safe_tool_error_for_a_denied_file(tmp_path: Path) -> None:
    config = tmp_path / "files.yaml"
    config.write_text(
        "version: 1\nplugins: [{name: files.local}, {name: tools.workspace}]\n"
    )
    (tmp_path / ".env").write_text("DO_NOT_EXPOSE=secret")

    async def scenario() -> None:
        catalog = agent_catalog(workspace=tmp_path, environment={})
        async with PluginHost(catalog.load(config)) as host:
            read = next(
                item for item in host.resolve(TOOLS) if item.name == "read_file"
            )
            result = await read.ainvoke(
                {
                    "type": "tool_call",
                    "id": "denied",
                    "name": "read_file",
                    "args": {"path": ".env"},
                }
            )
            assert result.status == "error"
            assert "protected" in result.content
            assert "DO_NOT_EXPOSE" not in result.content

    asyncio.run(scenario())


def test_same_agent_tools_work_with_a_different_file_provider(tmp_path: Path) -> None:
    class AlternateFiles:
        async def read(self, path, *, start_line=1, limit=200):
            return FileRead(
                "code.py",
                (FileLine(1, "def answer():"), FileLine(2, "    return 99")),
                False,
            )

        async def list(self, path=".", *, limit=200):
            return FileList(("code.py",), False)

        async def search(self, query, path=".", *, limit=200):
            return FileSearch((SearchHit("code.py", 1, "def answer():"),), False)

    class AlternatePlugin:
        async def activate(self, context):
            context.provide(WORKSPACE_FILES, AlternateFiles())

    alternate = PluginDefinition(
        "files.alternate", lambda config: AlternatePlugin, provides=(WORKSPACE_FILES,)
    )
    config = agent_config(tmp_path)
    config.write_text(config.read_text().replace("files.local", "files.alternate"))

    async def scenario() -> None:
        catalog = agent_catalog(
            (TEST_MODEL, alternate), workspace=tmp_path, environment={}
        )
        async with PluginHost(catalog.load(config)) as host:
            result = await AgentSession(host.resolve(AGENT_RUNTIME), tmp_path).ask(
                "Find answer"
            )
            assert result.text == "code.py:2:     return 99"
            assert [item.name for item in result.tools] == ["search_files", "read_file"]

    asyncio.run(scenario())
