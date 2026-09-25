"""Dynamic behavior is observed through host status, Context, and services."""

import asyncio

from bridge_agent.contracts.plugins import PluginDefinition, ServiceKey
from bridge_agent.kernel.dynamic import DynamicHost

VALUE = ServiceKey[str]("test.value")
RESULT = ServiceKey[str]("test.result")


def provider(value="root", events=None):
    class Provider:
        async def activate(self, context):
            context.provide(VALUE, value)
            if events is not None:
                events.append("start:" + value)
                context.on_close(lambda: events.append("stop:" + value))

    return PluginDefinition("provider", lambda config: Provider, provides=(VALUE,))


def consumer(events=None):
    class Consumer:
        async def activate(self, context):
            value = context.require(VALUE)
            context.provide(RESULT, "seen:" + value)
            if events is not None:
                events.append("use:" + value)
                context.on_close(lambda: events.append("release:" + value))

    return PluginDefinition(
        "consumer", lambda config: Consumer, requires=(VALUE,), provides=(RESULT,)
    )


def test_plugin_waits_for_its_dependency_then_activates():
    async def scenario():
        async with DynamicHost() as host:
            await host.mount("consumer", consumer())
            assert host.status("consumer").state == "pending"
            assert host.status("consumer").missing == ("test.value",)
            await host.mount("provider", provider())
            assert host.status("consumer").state == "active"
            assert host.context.require(RESULT) == "seen:root"

    asyncio.run(scenario())


def test_isolated_instances_and_joined_labels_do_not_overwrite_root():
    async def scenario():
        async with DynamicHost() as host:
            child = host.context.isolate(VALUE, "child").isolate(RESULT, "child")
            joined = host.context.isolate(VALUE, "child")
            await host.mount("root", provider("root"))
            await host.mount("child", provider("child"), context=child)
            await host.mount("consumer", consumer(), context=child)
            assert host.context.require(VALUE) == "root"
            assert child.require(RESULT) == "seen:child"
            assert joined.require(VALUE) == "child"
            assert child.root is host.context

    asyncio.run(scenario())


def test_provider_removal_cleans_consumers_first_and_rearrival_reactivates():
    async def scenario():
        events = []
        async with DynamicHost() as host:
            await host.mount("consumer", consumer(events))
            await host.mount("first", provider("one", events))
            await host.unmount("first")
            assert events == ["start:one", "use:one", "release:one", "stop:one"]
            assert host.status("consumer").state == "pending"
            await host.mount("second", provider("two", events))
            assert host.context.require(RESULT) == "seen:two"
        assert events[-2:] == ["release:two", "stop:two"]

    asyncio.run(scenario())


def test_context_metadata_and_intercepts_inherit_without_mutating_parent():
    async def scenario():
        async with DynamicHost() as host:
            parent = host.context.extend({"tenant": "a"}).intercept(
                VALUE, {"timeout": 10, "mode": "base"}
            )
            child = parent.extend({"tenant": "b"}).intercept(VALUE, {"timeout": 20})
            assert child.metadata["tenant"] == "b"
            assert parent.metadata["tenant"] == "a"
            assert child.config_for(VALUE, {"retries": 1}, {"mode": "head"}) == {
                "retries": 1,
                "timeout": 20,
                "mode": "head",
            }
            assert parent.config_for(VALUE) == {"timeout": 10, "mode": "base"}
            assert host.context.config_for(VALUE) == {}

    asyncio.run(scenario())


def test_duplicate_provider_is_rejected_without_replacing_existing_service():
    import pytest

    from bridge_agent.contracts.errors import PluginProtocolError

    async def scenario():
        async with DynamicHost() as host:
            await host.mount("first", provider("one"))
            with pytest.raises(PluginProtocolError, match="provider"):
                await host.mount("second", provider("two"))
            assert host.context.require(VALUE) == "one"

    asyncio.run(scenario())


def test_failed_activation_rolls_back_and_exposes_failed_state():
    import pytest

    from bridge_agent.contracts.errors import PluginActivationError, PluginProtocolError

    events = []
    contexts = []

    class Broken:
        async def activate(self, context):
            contexts.append(context)
            context.on_close(lambda: events.append("closed"))
            context.provide(VALUE, "not-published")
            raise RuntimeError("activation")

    async def scenario():
        async with DynamicHost() as host:
            await host.mount("consumer", consumer())
            with pytest.raises(PluginActivationError):
                await host.mount(
                    "broken",
                    PluginDefinition(
                        "broken", lambda config: Broken, provides=(VALUE,)
                    ),
                )
            assert host.status("broken").state == "failed"
            assert host.status("consumer").state == "pending"
            assert events == ["closed"]
            with pytest.raises(PluginProtocolError):
                host.context.require(VALUE)
            with pytest.raises(PluginProtocolError):
                contexts[0].on_close(lambda: None)

    asyncio.run(scenario())


def test_cancelled_activation_propagates_cancellation_after_cleanup():
    import pytest

    started = asyncio.Event()
    events = []

    class Slow:
        async def activate(self, context):
            context.on_close(lambda: events.append("closed"))
            started.set()
            await asyncio.Event().wait()

    async def scenario():
        async with DynamicHost() as host:
            task = asyncio.create_task(
                host.mount("slow", PluginDefinition("slow", lambda config: Slow))
            )
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert events == ["closed"]
            assert host.status("slow").state == "failed"

    asyncio.run(scenario())
