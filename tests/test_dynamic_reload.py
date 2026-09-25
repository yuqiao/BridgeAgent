"""Replacement, rollback, and in-flight calls at the dynamic host boundary."""

import asyncio

import pytest

from bridge_agent.contracts.plugins import PluginDefinition, ServiceKey
from bridge_agent.kernel.dynamic import DynamicHost

VALUE = ServiceKey[str]("reload.value")
RESULT = ServiceKey[str]("reload.result")


def provider(events):
    def prepare(config):
        value = config["value"]

        class Plugin:
            async def activate(self, context):
                events.append("start:" + value)
                context.on_close(lambda: events.append("stop:" + value))
                if value == "broken":
                    raise ValueError("broken")
                context.provide(VALUE, value)

        return Plugin

    return PluginDefinition("provider", prepare, provides=(VALUE,))


def test_reconfigure_replaces_the_provider_and_reactivates_consumers():
    events = []

    class Consumer:
        async def activate(self, context):
            context.provide(RESULT, "seen:" + context.require(VALUE))

    async def scenario():
        async with DynamicHost() as host:
            await host.mount("provider", provider(events), {"value": "one"})
            await host.mount(
                "consumer",
                PluginDefinition(
                    "consumer",
                    lambda config: Consumer,
                    requires=(VALUE,),
                    provides=(RESULT,),
                ),
            )
            await host.reconfigure("provider", {"value": "two"})
            assert host.context.require(RESULT) == "seen:two"
            assert events == ["start:one", "stop:one", "start:two"]

    asyncio.run(scenario())


def test_failed_replacement_restores_old_provider_and_reports_failure():
    from bridge_agent.contracts.errors import PluginActivationError

    events = []

    async def scenario():
        async with DynamicHost() as host:
            await host.mount("provider", provider(events), {"value": "one"})
            with pytest.raises(PluginActivationError):
                await host.reconfigure("provider", {"value": "broken"})
            assert host.context.require(VALUE) == "one"
            assert host.status("provider").state == "active"
            assert events == [
                "start:one",
                "stop:one",
                "start:broken",
                "stop:broken",
                "start:one",
            ]

    asyncio.run(scenario())


def test_replacement_waits_for_inflight_lease_and_timeout_keeps_old_service():
    from bridge_agent.contracts.errors import HostStateError

    events = []

    async def scenario():
        async with DynamicHost(drain_timeout=0.02) as host:
            await host.mount("provider", provider(events), {"value": "one"})
            async with host.lease() as context:
                assert context.require(VALUE) == "one"
                with pytest.raises(HostStateError, match="in-flight"):
                    await host.reconfigure("provider", {"value": "two"})
                assert events == ["start:one"]
            await host.reconfigure("provider", {"value": "two"})
            assert host.context.require(VALUE) == "two"

    asyncio.run(scenario())


def test_reload_uses_new_factory_with_existing_config():
    events = []

    class Updated:
        def __init__(self, value):
            self.value = value

        async def activate(self, context):
            context.provide(VALUE, "new:" + self.value)

    definition = PluginDefinition(
        "provider", lambda config: lambda: Updated(config["value"]), provides=(VALUE,)
    )

    async def scenario():
        async with DynamicHost() as host:
            await host.mount("provider", provider(events), {"value": "one"})
            await host.reload("provider", definition)
            assert host.context.require(VALUE) == "new:one"

    asyncio.run(scenario())


def test_volatile_config_updates_reference_without_restarting_and_invalid_keeps_old():
    views = []
    starts = []

    class Plugin:
        async def activate(self, context):
            starts.append("start")
            views.append(context.config)

    def validate(config):
        value = {"mode": "normal", "temperature": 1, **config}
        if value["temperature"] < 0:
            raise ValueError("invalid")
        return value

    definition = PluginDefinition(
        "live",
        lambda config: Plugin,
        volatile_fields=("temperature",),
        validate_config=validate,
    )

    async def scenario():
        async with DynamicHost() as host:
            await host.mount("live", definition)
            await host.reconfigure("live", {"temperature": 2})
            assert starts == ["start"]
            assert views[0]["temperature"] == 2
            with pytest.raises(ValueError):
                await host.reconfigure("live", {"temperature": -1})
            assert views[0]["temperature"] == 2
            await host.reconfigure("live", {"mode": "other", "temperature": 3})
            assert starts == ["start", "start"]
            assert views[1]["mode"] == "other"

    asyncio.run(scenario())


def test_lifecycle_events_and_update_veto_are_observable():
    events = []

    class Observer:
        async def activate(self, context):
            context.on(
                "internal.status",
                lambda status, previous: events.append(
                    (status.instance_id, status.state)
                ),
            )
            context.on(
                "internal.update",
                lambda instance_id, config, next: (
                    False if config.get("value") == "veto" else next()
                ),
            )

    async def scenario():
        async with DynamicHost() as host:
            await host.mount(
                "observer", PluginDefinition("observer", lambda config: Observer)
            )
            await host.mount("provider", provider([]), {"value": "one"})
            await host.reconfigure("provider", {"value": "veto"})
            assert host.context.require(VALUE) == "one"
            assert ("provider", "loading") in events
            assert ("provider", "active") in events

    asyncio.run(scenario())


def test_configuration_hook_transforms_before_validation():
    class ConfigHook:
        async def activate(self, context):
            context.on(
                "internal.config",
                lambda ident, config, next: (
                    {**config, "value": "transformed"}
                    if ident == "provider"
                    else next()
                ),
            )

    async def scenario():
        async with DynamicHost() as host:
            await host.mount(
                "hook", PluginDefinition("hook", lambda config: ConfigHook)
            )
            await host.mount("provider", provider([]), {"value": "raw"})
            assert host.context.require(VALUE) == "transformed"

    asyncio.run(scenario())


def test_service_availability_and_replacement_refresh_consumers():
    available = [True]

    class Provider:
        async def activate(self, context):
            context.provide(VALUE, "one", check=lambda: available[0])

    class Consumer:
        async def activate(self, context):
            context.provide(RESULT, context.require(VALUE))

    async def scenario():
        async with DynamicHost() as host:
            await host.mount(
                "provider",
                PluginDefinition(
                    "provider", lambda config: Provider, provides=(VALUE,)
                ),
            )
            await host.mount(
                "consumer",
                PluginDefinition(
                    "consumer",
                    lambda config: Consumer,
                    requires=(VALUE,),
                    provides=(RESULT,),
                ),
            )
            available[0] = False
            await host.refresh()
            assert host.status("consumer").state == "pending"
            available[0] = True
            await host.refresh()
            assert host.context.require(RESULT) == "one"
            await host.set_service("provider", VALUE, "two")
            assert host.context.require(RESULT) == "two"

    asyncio.run(scenario())


def test_cancelled_close_finishes_cleanup_before_propagating_cancellation():
    started = asyncio.Event()
    finish = asyncio.Event()
    released = []

    class Plugin:
        async def activate(self, context):
            async def cleanup():
                started.set()
                await finish.wait()
                released.append("done")

            context.on_close_async(cleanup)

    async def scenario():
        host = DynamicHost()
        await host.mount(
            "resource", PluginDefinition("resource", lambda config: Plugin)
        )
        task = asyncio.create_task(host.close())
        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert released == ["done"]
        assert host.status("resource").state == "disposed"

    asyncio.run(scenario())


def test_closed_host_cannot_be_reconfigured_or_lease_again():
    from bridge_agent.contracts.errors import HostStateError

    async def scenario():
        host = DynamicHost()
        await host.mount("provider", provider([]), {"value": "one"})
        await host.close()
        await host.close()
        with pytest.raises(HostStateError, match="closed"):
            await host.reconfigure("provider", {"value": "two"})
        with pytest.raises(HostStateError, match="closed"):
            async with host.lease():
                pass

    asyncio.run(scenario())


def test_consumer_failure_during_replacement_restores_the_entire_dependency_chain():
    from bridge_agent.contracts.errors import PluginActivationError

    class Consumer:
        async def activate(self, context):
            value = context.require(VALUE)
            if value == "two":
                raise RuntimeError("consumer rejects new provider")
            context.provide(RESULT, value)

    async def scenario():
        async with DynamicHost() as host:
            await host.mount("provider", provider([]), {"value": "one"})
            await host.mount(
                "consumer",
                PluginDefinition(
                    "consumer",
                    lambda config: Consumer,
                    requires=(VALUE,),
                    provides=(RESULT,),
                ),
            )
            with pytest.raises(PluginActivationError):
                await host.reconfigure("provider", {"value": "two"})
            assert host.context.require(RESULT) == "one"

    asyncio.run(scenario())


def test_registry_snapshot_reports_states_without_exposing_mutable_instances():
    async def scenario():
        async with DynamicHost() as host:
            await host.mount("provider", provider([]), {"value": "one"})
            assert [(item.instance_id, item.state) for item in host.instances] == [
                ("provider", "active")
            ]
            await host.unmount("provider")
            assert host.instances[0].state == "disposed"

    asyncio.run(scenario())


def test_reload_cannot_claim_another_active_providers_service():
    from bridge_agent.contracts.errors import PluginProtocolError

    class Other:
        async def activate(self, context):
            context.provide(RESULT, "other")

    definition = PluginDefinition("other", lambda config: Other, provides=(RESULT,))

    async def scenario():
        async with DynamicHost() as host:
            await host.mount("provider", provider([]), {"value": "one"})
            await host.mount("other", definition)
            with pytest.raises(PluginProtocolError, match="provider"):
                await host.reload("provider", definition)
            assert host.context.require(VALUE) == "one"
            assert host.context.require(RESULT) == "other"

    asyncio.run(scenario())


def test_explicit_reload_restarts_a_volatile_plugin_even_when_config_is_unchanged():
    starts = []

    class Plugin:
        async def activate(self, context):
            starts.append("start")

    definition = PluginDefinition(
        "live", lambda config: Plugin, volatile_fields=("value",)
    )

    async def scenario():
        async with DynamicHost() as host:
            await host.mount("live", definition, {"value": 1})
            await host.reload("live")
            assert starts == ["start", "start"]

    asyncio.run(scenario())


def test_nested_lease_does_not_deadlock_behind_a_waiting_update():
    waiting = asyncio.Event()

    async def scenario():
        async with DynamicHost(drain_timeout=1) as host:
            await host.mount("provider", provider([]), {"value": "one"})

            async def update():
                waiting.set()
                await host.reconfigure("provider", {"value": "two"})

            async with host.lease():
                task = asyncio.create_task(update())
                await waiting.wait()
                async with asyncio.timeout(0.1), host.lease() as context:
                    assert context.require(VALUE) == "one"
            await task
            assert host.context.require(VALUE) == "two"

    asyncio.run(scenario())
