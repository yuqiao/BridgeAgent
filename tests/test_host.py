"""Host behavior observed by real plugins and application callers."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from io import BytesIO

import pytest

from bridge_agent.contracts.errors import (
    DependencyError,
    HostStateError,
    PluginActivationError,
    PluginProtocolError,
)
from bridge_agent.contracts.plugins import (
    PluginContext,
    PreparedPlugin,
    ServiceIdentity,
    ServiceKey,
)
from bridge_agent.kernel.host import HostState, PluginHost


@dataclass
class FunctionPlugin:
    action: Callable[[PluginContext], Awaitable[None]]

    async def activate(self, context: PluginContext) -> None:
        await self.action(context)


def plugin(
    name: str,
    action: Callable[[PluginContext], Awaitable[None]],
    *,
    requires: tuple[ServiceIdentity, ...] = (),
    provides: tuple[ServiceIdentity, ...] = (),
) -> PreparedPlugin:
    return PreparedPlugin(name, lambda: FunctionPlugin(action), requires, provides)


def test_closing_an_unused_host_prevents_later_activation() -> None:
    async def scenario() -> None:
        host = PluginHost(())
        await host.close()
        await host.close()
        assert host.state is HostState.CLOSED
        with pytest.raises(HostStateError):
            await host.start()

    asyncio.run(scenario())


def test_application_and_cleanup_errors_are_both_reported() -> None:
    async def scenario() -> None:
        resource = BytesIO()
        application_error = ValueError("application failed")
        cleanup_error = OSError("release failed")

        async def activate(context: PluginContext) -> None:
            context.enter_context(resource)

            def fail_cleanup() -> None:
                raise cleanup_error

            context.on_close(fail_cleanup)

        host = PluginHost((plugin("resource", activate),))
        with pytest.raises(ExceptionGroup) as failure:
            async with host:
                raise application_error
        assert failure.value.exceptions[0] is application_error
        cleanup_group = failure.value.exceptions[1]
        assert isinstance(cleanup_group, ExceptionGroup)
        assert cleanup_group.exceptions[0].__cause__ is cleanup_error
        assert resource.closed
        assert host.state is HostState.CLOSED

    asyncio.run(scenario())


def test_consumer_can_use_provider_during_cleanup_and_resources_close_once() -> None:
    async def scenario() -> None:
        data = ServiceKey[BytesIO]("data")
        buffer = BytesIO()
        saved: list[bytes] = []

        async def provider(context: PluginContext) -> None:
            context.enter_context(buffer)
            context.on_close(lambda: saved.append(buffer.getvalue()))
            context.provide(data, buffer)

        async def consumer(context: PluginContext) -> None:
            stream = context.require(data)
            stream.write(b"hello")

            def finish() -> None:
                context.require(data).write(b" done")

            context.on_close(finish)

        host = PluginHost(
            (
                plugin("consumer", consumer, requires=(data,)),
                plugin("provider", provider, provides=(data,)),
            )
        )
        await host.start()
        assert host.resolve(data).getvalue() == b"hello"
        await host.close()
        await host.close()
        assert saved == [b"hello done"]
        assert buffer.closed
        assert host.registered_services == ()
        assert host.state is HostState.CLOSED

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "case",
    ["missing", "duplicate", "self", "cycle", "identity", "plugin", "declaration"],
)
def test_invalid_dependency_plan_never_activates_even_an_independent_plugin(
    case: str,
) -> None:
    async def scenario() -> None:
        a, b = ServiceKey[str]("a"), ServiceKey[str]("b")
        activated: list[str] = []

        async def activate(context: PluginContext) -> None:
            activated.append("unexpected activation")

        bad_plans = {
            "missing": (plugin("missing", activate, requires=(a,)),),
            "duplicate": (
                plugin("one", activate, provides=(a,)),
                plugin("two", activate, provides=(a,)),
            ),
            "self": (plugin("self", activate, requires=(a,), provides=(a,)),),
            "cycle": (
                plugin("one", activate, requires=(b,), provides=(a,)),
                plugin("two", activate, requires=(a,), provides=(b,)),
            ),
            "identity": (
                plugin("one", activate, provides=(a,)),
                plugin("two", activate, requires=(ServiceKey[str]("a"),)),
            ),
            "plugin": (plugin("same", activate), plugin("same", activate)),
            "declaration": (plugin("one", activate, provides=(a, a)),),
        }
        host = PluginHost((plugin("independent", activate), *bad_plans[case]))
        with pytest.raises(DependencyError):
            await host.start()
        assert activated == []
        assert host.state is HostState.FAILED
        assert host.registered_services == ()
        await host.close()

    asyncio.run(scenario())


def test_partial_activation_rolls_back_owned_resources_and_preserves_both_failures() -> (
    None
):
    async def scenario() -> None:
        data = ServiceKey[BytesIO]("data")
        first, second, third = BytesIO(), BytesIO(), BytesIO()
        released: list[str] = []
        original = ValueError("activation fault")
        cleanup_fault = OSError("cleanup fault")

        async def provider(context: PluginContext) -> None:
            context.enter_context(first)
            context.on_close(lambda: released.append("provider"))
            context.provide(data, first)

        async def failing(context: PluginContext) -> None:
            context.require(data).write(b"used")
            context.enter_context(second)
            context.on_close(lambda: released.append("second"))
            context.enter_context(third)

            def fail_cleanup() -> None:
                released.append("third")
                raise cleanup_fault

            context.on_close(fail_cleanup)
            raise original

        async def must_not_run(context: PluginContext) -> None:
            pytest.fail("A later plugin was activated after startup failed")

        host = PluginHost(
            (
                plugin("provider", provider, provides=(data,)),
                plugin("failing", failing, requires=(data,)),
                plugin("later", must_not_run, requires=(data,)),
            )
        )
        with pytest.raises(ExceptionGroup) as failure:
            await host.start()
        errors = failure.value.exceptions
        assert isinstance(errors[0], PluginActivationError)
        assert errors[0].__cause__ is original
        assert errors[1].__cause__ is cleanup_fault
        assert released == ["third", "second", "provider"]
        assert all(resource.closed for resource in (first, second, third))
        assert host.registered_services == ()
        assert host.state is HostState.FAILED

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "violation", ["missing", "extra", "twice", "undeclared-dependency"]
)
def test_protocol_violation_never_publishes_partial_services(violation: str) -> None:
    async def scenario() -> None:
        a, b = ServiceKey[str]("a"), ServiceKey[str]("b")
        resource = BytesIO()

        async def activate(context: PluginContext) -> None:
            context.enter_context(resource)
            if violation == "missing":
                return
            context.provide(a, "staged")
            assert host.registered_services == ()
            if violation == "extra":
                context.provide(b, "undeclared")
            elif violation == "twice":
                context.provide(a, "replacement")
            else:
                context.require(b)

        host = PluginHost((plugin("broken", activate, provides=(a,)),))
        with pytest.raises(PluginActivationError) as failure:
            await host.start()
        assert isinstance(failure.value.__cause__, PluginProtocolError)
        assert resource.closed
        assert host.registered_services == ()

    asyncio.run(scenario())


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_cancelled_start_waits_for_rollback_before_propagating_cancellation(
    cleanup_fails: bool,
) -> None:
    async def scenario() -> None:
        entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        resource = BytesIO()

        async def activate(context: PluginContext) -> None:
            context.enter_context(resource)

            async def cleanup() -> None:
                cleaning.set()
                await release.wait()
                if cleanup_fails:
                    raise OSError("release failed")

            context.on_close_async(cleanup)
            entered.set()
            await asyncio.Event().wait()

        host = PluginHost((plugin("waiting", activate),))
        start = asyncio.create_task(host.start())
        await entered.wait()
        with pytest.raises(HostStateError):
            await host.close()
        with pytest.raises(HostStateError):
            await host.start()
        start.cancel()
        await cleaning.wait()
        assert not start.done()
        assert not resource.closed
        start.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError) as failure:
            await start
        assert resource.closed
        assert host.state is HostState.FAILED
        assert host.registered_services == ()
        if cleanup_fails:
            assert isinstance(failure.value.__cause__, ExceptionGroup)
        assert not [
            task for task in asyncio.all_tasks() if task is not asyncio.current_task()
        ]

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_concurrent_close_waiters_share_cleanup_despite_repeated_cancellation(
    cleanup_fails: bool,
) -> None:
    async def scenario() -> None:
        cleaning, release, joined = asyncio.Event(), asyncio.Event(), asyncio.Event()
        resource = BytesIO()
        completions: list[str] = []

        async def activate(context: PluginContext) -> None:
            context.enter_context(resource)

            async def cleanup() -> None:
                cleaning.set()
                await release.wait()
                completions.append("released")
                if cleanup_fails:
                    raise OSError("cleanup failed")

            context.on_close_async(cleanup)

        host = PluginHost((plugin("resource", activate),))
        await host.start()
        first = asyncio.create_task(host.close())
        await cleaning.wait()

        async def join() -> None:
            joined.set()
            await host.close()

        second = asyncio.create_task(join())
        await joined.wait()
        first.cancel()
        await asyncio.sleep(0)  # Yield once so the first cancellation is delivered.
        first.cancel()
        assert not resource.closed
        assert not second.done()
        release.set()
        with pytest.raises(asyncio.CancelledError) as failure:
            await first
        if cleanup_fails:
            assert isinstance(failure.value.__cause__, ExceptionGroup)
            with pytest.raises(ExceptionGroup):
                await second
        else:
            await second
        await host.close()
        assert resource.closed
        assert completions == ["released"]
        assert host.state is HostState.CLOSED
        assert not [
            task for task in asyncio.all_tasks() if task is not asyncio.current_task()
        ]

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))


def test_context_cannot_register_after_activation_or_read_after_its_owner_closes() -> (
    None
):
    async def scenario() -> None:
        contexts: list[PluginContext] = []
        value = ServiceKey[str]("value")

        async def activate(context: PluginContext) -> None:
            contexts.append(context)
            context.provide(value, "ready")

        host = PluginHost((plugin("provider", activate, provides=(value,)),))
        with pytest.raises(HostStateError):
            host.resolve(value)
        await host.start()
        context = contexts[0]
        with pytest.raises(PluginProtocolError):
            context.provide(value, "late")
        with pytest.raises(PluginProtocolError):
            context.on_close(lambda: None)
        await host.close()
        with pytest.raises(PluginProtocolError, match="closed"):
            context.require(value)
        with pytest.raises(HostStateError):
            host.resolve(value)
        with pytest.raises(HostStateError):
            await host.start()

    asyncio.run(scenario())


def test_registration_during_cleanup_fails_without_skipping_other_resources() -> None:
    async def scenario() -> None:
        resource = BytesIO()

        async def activate(context: PluginContext) -> None:
            context.enter_context(resource)
            context.on_close(lambda: context.on_close(lambda: None))

        host = PluginHost((plugin("late-registration", activate),))
        await host.start()
        with pytest.raises(ExceptionGroup) as failure:
            await host.close()
        assert isinstance(failure.value.exceptions[0].__cause__, PluginProtocolError)
        assert resource.closed
        assert host.state is HostState.CLOSED

    asyncio.run(scenario())


def test_factory_failure_happens_before_any_plugin_is_activated() -> None:
    async def scenario() -> None:
        activated: list[str] = []

        async def activate(context: PluginContext) -> None:
            activated.append("unexpected")

        def broken_factory() -> FunctionPlugin:
            raise ValueError("constructor failed")

        host = PluginHost(
            (plugin("first", activate), PreparedPlugin("broken", broken_factory))
        )
        with pytest.raises(PluginActivationError, match="broken: construction failed"):
            await host.start()
        assert activated == []
        assert host.state is HostState.FAILED
        assert host.registered_services == ()

    asyncio.run(scenario())


def test_independent_plugins_activate_in_input_order() -> None:
    async def scenario() -> None:
        observed: list[str] = []

        async def alpha(context: PluginContext) -> None:
            observed.append("alpha")

        async def beta(context: PluginContext) -> None:
            observed.append("beta")

        for _ in range(2):
            async with PluginHost((plugin("beta", beta), plugin("alpha", alpha))):
                pass
        assert observed == ["beta", "alpha", "beta", "alpha"]

    asyncio.run(scenario())


def test_async_resource_context_is_owned_until_host_closes() -> None:
    async def scenario() -> None:
        data = ServiceKey[BytesIO]("async-data")
        buffer = BytesIO(b"async resource")

        @asynccontextmanager
        async def resource() -> AsyncIterator[BytesIO]:
            try:
                yield buffer
            finally:
                buffer.close()

        async def activate(context: PluginContext) -> None:
            context.provide(data, await context.enter_async_context(resource()))

        async with PluginHost(
            (plugin("resource", activate, provides=(data,)),)
        ) as host:
            assert host.resolve(data).getvalue() == b"async resource"
        assert buffer.closed

    asyncio.run(scenario())
