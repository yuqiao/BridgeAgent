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


def test_service_get_set_and_dispatch_extension_points():
    from bridge_agent.contracts.plugins import ServiceKey

    key = ServiceKey[str]("intercepted")
    seen = []

    class Observer:
        async def activate(self, context):
            context.on(
                "internal.get",
                lambda ctx, target, next: next().upper() if target is key else next(),
            )
            context.on(
                "internal.set",
                lambda ctx, target, value, next: (seen.append(value), next())[1],
            )
            context.on("internal.dispatch", lambda mode, name, args: seen.append(name))

    class Provider:
        async def activate(self, context):
            context.provide(key, "hello")

    async def scenario():
        async with DynamicHost() as host:
            await host.mount(
                "observer", PluginDefinition("observer", lambda config: Observer)
            )
            await host.mount(
                "value",
                PluginDefinition("value", lambda config: Provider, provides=(key,)),
            )
            assert host.context.require(key) == "HELLO"
            host.context.emit("custom")
            assert seen == ["hello", "custom"]

    asyncio.run(scenario())


def test_unmount_waits_for_an_effect_disposal_already_in_progress():
    import pytest

    started = asyncio.Event()
    finish = asyncio.Event()
    disposers = []

    class Plugin:
        async def activate(self, context):
            async def cleanup():
                started.set()
                await finish.wait()

            disposers.append(await context.effect(lambda: cleanup))

    async def scenario():
        async with DynamicHost() as host:
            await host.mount(
                "effect", PluginDefinition("effect", lambda config: Plugin)
            )
            disposing = asyncio.create_task(disposers[0]())
            await started.wait()
            unmounting = asyncio.create_task(host.unmount("effect"))
            try:
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(unmounting), 0.02)
            finally:
                finish.set()
                await asyncio.gather(disposing, unmounting)

    asyncio.run(scenario())


def test_listener_registration_can_be_replaced_with_owned_disposer():
    calls = []

    class Hook:
        async def activate(self, context):
            def intercept(owner, name, callback):
                if name == "blocked":
                    calls.append("intercepted")
                    return lambda: calls.append("disposed")
                return None

            context.on("internal.listener", intercept)

    class Listener:
        async def activate(self, context):
            context.on("blocked", lambda: calls.append("called"))

    async def scenario():
        async with DynamicHost() as host:
            await host.mount("hook", PluginDefinition("hook", lambda config: Hook))
            await host.mount(
                "listener", PluginDefinition("listener", lambda config: Listener)
            )
            host.context.emit("blocked")
            await host.unmount("listener")
            assert calls == ["intercepted", "disposed"]

    asyncio.run(scenario())


def test_async_event_dispatch_holds_its_listeners_alive_until_completion():
    import pytest

    from bridge_agent.contracts.errors import HostStateError

    started = asyncio.Event()
    finish = asyncio.Event()

    class Plugin:
        async def activate(self, context):
            async def listener():
                started.set()
                await finish.wait()

            context.on("work", listener)

    async def scenario():
        async with DynamicHost(drain_timeout=0.02) as host:
            await host.mount(
                "listener", PluginDefinition("listener", lambda config: Plugin)
            )
            task = asyncio.create_task(host.context.parallel("work"))
            await started.wait()
            try:
                with pytest.raises(HostStateError):
                    await host.unmount("listener")
            finally:
                finish.set()
                await task

    asyncio.run(scenario())


def test_async_cleanup_can_dispatch_owned_events_without_deadlock():
    import subprocess
    import sys

    script = """
import asyncio
from bridge_agent.kernel.dynamic import DynamicHost
from bridge_agent.contracts.plugins import PluginDefinition
class Plugin:
    async def activate(self, context):
        context.on('cleanup', lambda: print('released'))
        context.on_close_async(lambda: context.parallel('cleanup'))
async def main():
    async with DynamicHost() as host:
        await host.mount('plugin', PluginDefinition('plugin', lambda config: Plugin))
asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script], capture_output=True, text=True, timeout=3
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "released\n"


def test_pending_effect_acquisition_is_drained_before_unmount():
    contexts = []
    released = []

    class Plugin:
        async def activate(self, context):
            contexts.append(context)

    async def scenario():
        started, finish = asyncio.Event(), asyncio.Event()
        async with DynamicHost() as host:
            await host.mount(
                "resource", PluginDefinition("resource", lambda config: Plugin)
            )

            async def acquire():
                started.set()
                await finish.wait()
                return lambda: released.append("closed")

            acquisition = asyncio.create_task(contexts[0].effect(acquire))
            await started.wait()
            removal = asyncio.create_task(host.unmount("resource"))
            await asyncio.sleep(0)
            assert not removal.done()
            finish.set()
            await acquisition
            await removal
            assert released == ["closed"]
            assert host.status("resource").state == "disposed"

    asyncio.run(scenario())
