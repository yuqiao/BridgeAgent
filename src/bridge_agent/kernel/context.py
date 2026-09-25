"""Plugin-local contributions and resource ownership; no global service locator."""

from collections.abc import Awaitable, Callable
from contextlib import (
    AbstractAsyncContextManager,
    AbstractContextManager,
    AsyncExitStack,
)
from enum import Enum, auto
from typing import cast

from bridge_agent.contracts.errors import PluginCleanupError, PluginProtocolError
from bridge_agent.contracts.plugins import PreparedPlugin, ServiceIdentity, ServiceKey


class ContextState(Enum):
    ACTIVATING = auto()
    ACTIVE = auto()
    CLOSING = auto()
    CLOSED = auto()


class OwnedContext:
    """A host-owned context whose contributions publish only after activation."""

    def __init__(
        self,
        plugin: PreparedPlugin,
        services: dict[ServiceIdentity, object],
    ) -> None:
        self.plugin = plugin
        self.state = ContextState.ACTIVATING
        self._services = services
        self._pending: dict[ServiceIdentity, object] = {}
        self._resources = AsyncExitStack()
        self._errors: list[Exception] = []

    def _assert_activating(self) -> None:
        if self.state is not ContextState.ACTIVATING:
            raise PluginProtocolError(
                f"Plugin {self.plugin.name}: registrations require ACTIVATING, "
                f"got {self.state.name}"
            )

    def require[T](self, key: ServiceKey[T]) -> T:
        if self.state is ContextState.CLOSED:
            raise PluginProtocolError(f"Plugin {self.plugin.name}: context is closed")
        if key not in self.plugin.requires:
            raise PluginProtocolError(
                f"Plugin {self.plugin.name}: undeclared dependency {key.name}"
            )
        if key not in self._services:
            raise PluginProtocolError(
                f"Plugin {self.plugin.name}: service unavailable: {key.name}"
            )
        # The canonical key's T is preserved at provide(); only storage erases it.
        return cast(T, self._services[key])

    def provide[T](self, key: ServiceKey[T], value: T) -> None:
        self._assert_activating()
        if key not in self.plugin.provides:
            raise PluginProtocolError(
                f"Plugin {self.plugin.name}: undeclared service {key.name}"
            )
        if key in self._pending:
            raise PluginProtocolError(
                f"Plugin {self.plugin.name}: service registered twice: {key.name}"
            )
        self._pending[key] = value

    def on_close(self, callback: Callable[[], None]) -> None:
        async def invoke() -> None:
            callback()

        self.on_close_async(invoke)

    def on_close_async(self, callback: Callable[[], Awaitable[None]]) -> None:
        self._assert_activating()

        async def release() -> None:
            try:
                await callback()
            except BaseException as cause:
                # A callback failure (including its own cancellation) must not skip
                # other resources. Caller cancellation is handled by the host.
                error = PluginCleanupError(f"Plugin {self.plugin.name}: cleanup failed")
                error.__cause__ = cause
                self._errors.append(error)

        self._resources.push_async_callback(release)

    def enter_context[T](self, manager: AbstractContextManager[T]) -> T:
        self._assert_activating()
        value = manager.__enter__()

        def exit_resource() -> None:
            manager.__exit__(None, None, None)

        try:
            self.on_close(exit_resource)
        except BaseException:
            exit_resource()
            raise
        return value

    async def enter_async_context[T](
        self, manager: AbstractAsyncContextManager[T]
    ) -> T:
        self._assert_activating()
        value = await manager.__aenter__()

        async def exit_resource() -> None:
            await manager.__aexit__(None, None, None)

        try:
            self.on_close_async(exit_resource)
        except BaseException:
            await exit_resource()
            raise
        return value

    def publish(self) -> None:
        self._assert_activating()
        missing = [key.name for key in self.plugin.provides if key not in self._pending]
        if missing:
            raise PluginProtocolError(
                f"Plugin {self.plugin.name}: declared services not provided: "
                + ", ".join(missing)
            )
        self._services.update(self._pending)
        self._pending.clear()
        self.state = ContextState.ACTIVE

    async def close(self) -> list[Exception]:
        self.state = ContextState.CLOSING
        await self._resources.aclose()
        for key in self.plugin.provides:
            self._services.pop(key, None)
        self._pending.clear()
        self.state = ContextState.CLOSED
        return self._errors
