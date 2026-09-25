"""Actual LangChain runtime behind a dynamically extensible Agent handle."""

import asyncio

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from bridge_agent.application.agent import AgentSession
from bridge_agent.contracts.agent import RunResult
from bridge_agent.contracts.plugins import PluginDefinition
from bridge_agent.kernel.dynamic import DynamicHost
from bridge_agent.plugins.dynamic_runtime import DynamicAgentRuntime
from bridge_agent.plugins.langchain.builtin import MEMORY, runtime_definition
from bridge_agent.plugins.langchain.services import MODEL, TOOLS


def test_agent_run_can_be_wrapped_by_a_plugin(tmp_path):
    class Model:
        async def activate(self, context):
            context.provide(
                MODEL,
                FakeMessagesListChatModel(
                    responses=[
                        AIMessage(content="answer", id="answer-1"),
                        AIMessage(content="answer", id="answer-2"),
                    ]
                ),
            )
            context.provide(TOOLS, ())

    class Extension:
        async def activate(self, context):
            async def around(request, next):
                result = await next()
                return RunResult("reviewed: " + result.text, result.tools)

            context.on("agent.run", around)

    async def scenario():
        async with DynamicHost() as host:
            await host.mount("runtime", runtime_definition(tmp_path))
            await host.mount(
                "model",
                PluginDefinition(
                    "model", lambda config: Model, provides=(MODEL, TOOLS)
                ),
            )
            await host.mount("memory", MEMORY)
            await host.mount(
                "extension", PluginDefinition("extension", lambda config: Extension)
            )
            session = AgentSession(DynamicAgentRuntime(host), tmp_path)
            assert (await session.ask("hello")).text == "reviewed: answer"
            await host.unmount("extension")
            assert (await session.ask("again")).text == "answer"

    asyncio.run(scenario())
