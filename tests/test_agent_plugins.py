"""Agent capabilities composed through YAML and the existing plugin host."""

import asyncio
from pathlib import Path

import pytest

from bridge_agent.application.agent import AgentSession
from bridge_agent.bootstrap.agent import agent_catalog
from bridge_agent.contracts.agent import AGENT_RUNTIME
from bridge_agent.contracts.plugins import PluginDefinition
from bridge_agent.kernel.host import PluginHost
from bridge_agent.plugins.langchain.services import MODEL
from tests.test_agent_runtime import CalculatorModel


def test_yaml_assembles_a_model_tool_checkpoint_and_runtime(tmp_path: Path) -> None:
    class ModelPlugin:
        async def activate(self, context):
            context.provide(MODEL, CalculatorModel())

    model = PluginDefinition(
        "test.model", lambda config: ModelPlugin, provides=(MODEL,)
    )
    path = tmp_path / "agent.yaml"
    path.write_text("""version: 1
plugins:
  - name: runtime.langchain
  - name: tools.arithmetic
  - name: checkpoint.memory
  - name: test.model
""")

    async def scenario() -> None:
        async with PluginHost(agent_catalog((model,)).load(path)) as host:
            session = AgentSession(host.resolve(AGENT_RUNTIME), tmp_path, "session-a")
            result = await session.ask("Add 19 and 23")
            assert result.text == "answer: 42"
            assert result.tools[0].content == "42"

    asyncio.run(scenario())


def test_application_uses_an_alternative_runtime_selected_by_yaml(
    tmp_path: Path,
) -> None:
    from bridge_agent.contracts.agent import RunResult

    class EchoRuntime:
        async def run(self, request):
            return RunResult("echo: " + request.text)

    class EchoPlugin:
        async def activate(self, context):
            context.provide(AGENT_RUNTIME, EchoRuntime())

    definition = PluginDefinition(
        "test.echo", lambda config: EchoPlugin, provides=(AGENT_RUNTIME,)
    )
    path = tmp_path / "echo.yaml"
    path.write_text("version: 1\nplugins: [{name: test.echo}]\n")

    async def scenario() -> None:
        async with PluginHost(agent_catalog((definition,)).load(path)) as host:
            session = AgentSession(host.resolve(AGENT_RUNTIME), tmp_path)
            assert (await session.ask("hello")).text == "echo: hello"

    asyncio.run(scenario())


def test_another_model_and_extended_tool_set_work_without_application_changes(
    tmp_path: Path,
) -> None:
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_core.tools import tool

    from bridge_agent.plugins.langchain.builtin import add
    from bridge_agent.plugins.langchain.services import TOOLS

    @tool
    async def multiply(a: int, b: int) -> str:
        """Multiply two integers."""
        return str(a * b)

    class ProductModel(CalculatorModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if isinstance(messages[-1], ToolMessage):
                return super()._generate(messages)
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "multiply",
                                    "args": {"a": 6, "b": 7},
                                    "id": "product-1",
                                }
                            ],
                        )
                    )
                ]
            )

    class ModelPlugin:
        async def activate(self, context):
            context.provide(MODEL, ProductModel())

    class ToolsPlugin:
        async def activate(self, context):
            context.provide(TOOLS, (add, multiply))

    model = PluginDefinition(
        "test.product-model", lambda config: ModelPlugin, provides=(MODEL,)
    )
    tools = PluginDefinition(
        "test.extended-tools", lambda config: ToolsPlugin, provides=(TOOLS,)
    )
    path = tmp_path / "extended.yaml"
    path.write_text("""version: 1
plugins:
  - name: runtime.langchain
  - name: checkpoint.memory
  - name: test.product-model
  - name: test.extended-tools
""")

    async def scenario() -> None:
        async with PluginHost(agent_catalog((model, tools)).load(path)) as host:
            result = await AgentSession(host.resolve(AGENT_RUNTIME), tmp_path).ask(
                "Multiply 6 and 7"
            )
            assert result.text == "answer: 42"
            assert [(item.name, item.content) for item in result.tools] == [
                ("multiply", "42")
            ]

    asyncio.run(scenario())


def test_runtime_model_budget_is_configured_through_yaml(tmp_path: Path) -> None:
    from bridge_agent.contracts.errors import AgentLimitError

    class ModelPlugin:
        async def activate(self, context):
            context.provide(MODEL, CalculatorModel())

    model = PluginDefinition(
        "test.model", lambda config: ModelPlugin, provides=(MODEL,)
    )
    path = tmp_path / "limited.yaml"
    path.write_text("""version: 1
plugins:
  - name: runtime.langchain
    config:
      max_model_calls: 1
      timeout_seconds: 10
      system_prompt: "Use arithmetic tools."
  - name: tools.arithmetic
  - name: checkpoint.memory
  - name: test.model
""")

    async def scenario() -> None:
        async with PluginHost(agent_catalog((model,)).load(path)) as host:
            with pytest.raises(AgentLimitError):
                await AgentSession(host.resolve(AGENT_RUNTIME), tmp_path).ask("Add")

    asyncio.run(scenario())
