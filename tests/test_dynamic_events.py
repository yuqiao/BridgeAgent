"""Event and contribution semantics through plugin-owned Contexts."""

import asyncio

from bridge_agent.contracts.plugins import PluginDefinition
from bridge_agent.kernel.dynamic import DynamicHost


def test_bail_stops_on_zero_and_unmount_revokes_listeners():
    calls = []

    class Listeners:
        async def activate(self, context):
            context.on("check", lambda: calls.append("first"))
            context.on("check", lambda: 0)
            context.on("check", lambda: calls.append("last"))

    async def scenario():
        async with DynamicHost() as host:
            await host.mount(
                "events", PluginDefinition("events", lambda config: Listeners)
            )
            assert host.context.bail("check") == 0
            assert calls == ["first"]
            await host.unmount("events")
            assert host.context.bail("check") is None
            assert calls == ["first"]

    asyncio.run(scenario())


def test_async_serial_short_circuit_and_parallel_error_aggregation():
    import pytest

    calls = []

    class Listeners:
        async def activate(self, context):
            async def first():
                calls.append("first")
                return False

            async def second():
                calls.append("second")
                return ""

            async def fail():
                calls.append("failed")
                raise ValueError("listener")

            context.on("work", first)
            context.on("work", second)
            context.on("work", fail)

    async def scenario():
        async with DynamicHost() as host:
            await host.mount(
                "events", PluginDefinition("events", lambda config: Listeners)
            )
            assert await host.context.serial("work") == ""
            assert calls == ["first", "second"]
            with pytest.raises(ExceptionGroup) as error:
                await host.context.parallel("work")
            assert len(error.value.exceptions) == 1
            assert calls[-3:] == ["first", "second", "failed"]

    asyncio.run(scenario())


def test_once_prepend_and_filtered_global_listeners():
    calls = []

    class Listeners:
        async def activate(self, context):
            context.on("note", lambda: calls.append("normal"))
            context.once("note", lambda: calls.append("once"), prepend=True)
            context.on("note", lambda: calls.append("global"), global_=True)

    async def scenario():
        async with DynamicHost() as host:
            await host.mount(
                "events",
                PluginDefinition("events", lambda config: Listeners),
                context=host.context.extend({"tenant": "a"}),
            )
            host.context.emit("note")
            assert calls == ["once", "normal", "global"]
            calls.clear()
            host.context.select(lambda ctx: ctx.metadata.get("tenant") == "b").emit(
                "note"
            )
            assert calls == ["global"]
            calls.clear()
            host.context.emit("note")
            assert calls == ["normal", "global"]

    asyncio.run(scenario())


def test_waterfall_wraps_next_and_can_veto_the_operation():
    calls = []

    class Around:
        async def activate(self, context):
            async def wrap(value, next):
                calls.append("before")
                result = await next()
                calls.append("after")
                return result + "!"

            context.on("run", wrap)
            context.on("veto", lambda value, next: "denied")

    async def scenario():
        async with DynamicHost() as host:
            await host.mount(
                "around", PluginDefinition("around", lambda config: Around)
            )

            async def inner():
                calls.append("inner")
                return "ok"

            assert await host.context.waterfall("run", "request", next=inner) == "ok!"
            assert calls == ["before", "inner", "after"]
            assert (
                await host.context.waterfall("veto", "request", next=inner) == "denied"
            )
            assert calls == ["before", "inner", "after"]

    asyncio.run(scenario())


def test_active_context_effects_can_be_revoked_once_and_are_diagnosable():
    contexts = []
    calls = []

    class Plugin:
        async def activate(self, context):
            contexts.append(context)

    async def scenario():
        async with DynamicHost() as host:
            await host.mount(
                "effects", PluginDefinition("effects", lambda config: Plugin)
            )
            context = contexts[0]
            dispose = await context.effect(
                lambda: lambda: calls.append("released"), label="test resource"
            )
            assert "test resource" in host.status("effects").effects
            await dispose()
            await dispose()
            assert calls == ["released"]
            assert host.status("effects").effects == ()
        assert calls == ["released"]

    asyncio.run(scenario())


def test_accessor_and_alias_follow_current_value_until_provider_unloads():
    from bridge_agent.contracts.plugins import ServiceKey

    key = ServiceKey[int]("computed")
    alias = ServiceKey[int]("alias")
    value = [1]

    class Provider:
        async def activate(self, context):
            context.accessor(key, lambda: value[0])

    class Alias:
        async def activate(self, context):
            context.alias(alias, key)

    async def scenario():
        async with DynamicHost() as host:
            await host.mount(
                "value",
                PluginDefinition("value", lambda config: Provider, provides=(key,)),
            )
            await host.mount(
                "alias",
                PluginDefinition(
                    "alias", lambda config: Alias, requires=(key,), provides=(alias,)
                ),
            )
            assert host.context.require(alias) == 1
            value[0] = 2
            assert host.context.require(alias) == 2
            await host.unmount("value")
            assert host.status("alias").state == "pending"

    asyncio.run(scenario())


def test_named_logging_exporter_is_removed_with_its_plugin():
    import logging

    records = []

    class Exporter(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    loggers = []

    class Plugin:
        async def activate(self, context):
            logger = context.logger("test-export")
            logger.setLevel(logging.INFO)
            context.export_logs(logger, Exporter())
            loggers.append(logger)

    async def scenario():
        async with DynamicHost() as host:
            await host.mount("logs", PluginDefinition("logs", lambda config: Plugin))
            loggers[0].info("before")
            await host.unmount("logs")
            loggers[0].info("after")
            assert records == ["before"]

    asyncio.run(scenario())


def test_old_disposer_cannot_remove_a_later_listener_with_the_same_name():
    disposers = []
    calls = []

    class First:
        async def activate(self, context):
            disposers.append(context.on("same", lambda: calls.append("first")))

    class Second:
        async def activate(self, context):
            context.on("same", lambda: calls.append("second"))

    async def scenario():
        async with DynamicHost() as host:
            await host.mount("first", PluginDefinition("first", lambda config: First))
            disposers[0]()
            await host.mount(
                "second", PluginDefinition("second", lambda config: Second)
            )
            await host.unmount("first")
            host.context.emit("same")
            assert calls == ["second"]

    asyncio.run(scenario())
