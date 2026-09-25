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


def test_running_agent_holds_a_lease_until_the_call_finishes(tmp_path):
    import pytest

    from bridge_agent.contracts.agent import AGENT_RUNTIME
    from bridge_agent.contracts.errors import HostStateError

    started = asyncio.Event()
    finish = asyncio.Event()

    class Runtime:
        async def run(self, request):
            started.set()
            await finish.wait()
            return RunResult("finished")

    class Plugin:
        async def activate(self, context):
            context.provide(AGENT_RUNTIME, Runtime())

    async def scenario():
        async with DynamicHost(drain_timeout=0.02) as host:
            await host.mount(
                "runtime",
                PluginDefinition(
                    "runtime", lambda config: Plugin, provides=(AGENT_RUNTIME,)
                ),
            )
            task = asyncio.create_task(
                AgentSession(DynamicAgentRuntime(host), tmp_path).ask("hello")
            )
            await started.wait()
            try:
                with pytest.raises(HostStateError, match="in-flight"):
                    await host.unmount("runtime")
            finally:
                finish.set()
                assert (await task).text == "finished"
            await host.unmount("runtime")

    asyncio.run(scenario())


def test_repeated_provider_and_extension_reload_preserves_scopes_and_releases_files(
    tmp_path,
):
    import tempfile

    from bridge_agent.contracts.agent import AGENT_RUNTIME
    from bridge_agent.contracts.sessions import SESSION_CONTROL
    from bridge_agent.plugins.langchain.services import CHECKPOINT

    handles = []
    calls = []

    def prepare(config):
        class Model:
            async def activate(self, context):
                handles.append(context.enter_context(tempfile.TemporaryFile()))
                context.provide(
                    MODEL,
                    FakeMessagesListChatModel(
                        responses=[AIMessage(content=config["answer"])]
                    ),
                )

        return Model

    model = PluginDefinition("model", prepare, provides=(MODEL,))

    class Tools:
        async def activate(self, context):
            context.provide(TOOLS, ())

    class Extension:
        async def activate(self, context):
            async def around(request, next):
                calls.append(request.text)
                return await next()

            context.on("agent.run", around)

    async def scenario():
        async with DynamicHost() as host:
            child = host.context
            for key in (MODEL, CHECKPOINT, AGENT_RUNTIME, SESSION_CONTROL):
                child = child.isolate(key, "child")
            await host.mount("runtime", runtime_definition(tmp_path))
            await host.mount(
                "child-runtime", runtime_definition(tmp_path), context=child
            )
            await host.mount("model", model, {"answer": "root"})
            await host.mount("child-model", model, {"answer": "child"}, context=child)
            await host.mount("memory", MEMORY)
            await host.mount("child-memory", MEMORY, context=child)
            await host.mount(
                "tools",
                PluginDefinition("tools", lambda config: Tools, provides=(TOOLS,)),
            )
            await host.mount(
                "extension", PluginDefinition("extension", lambda config: Extension)
            )
            isolated_runtime = child.require(AGENT_RUNTIME)
            for index in range(10):
                await host.reconfigure("model", {"answer": f"root-{index}"})
                await host.reload("extension")
                result = await AgentSession(DynamicAgentRuntime(host), tmp_path).ask(
                    f"question-{index}"
                )
                assert result.text == f"root-{index}"
                assert child.require(AGENT_RUNTIME) is isolated_runtime
                assert sum(not handle.closed for handle in handles) == 2
                assert (
                    len([item for item in host.instances if item.state == "active"])
                    == 8
                )
            result = await AgentSession(DynamicAgentRuntime(host, child), tmp_path).ask(
                "isolated"
            )
            assert result.text == "child"
            assert len(calls) == 11
        assert all(handle.closed for handle in handles)

    asyncio.run(scenario())
