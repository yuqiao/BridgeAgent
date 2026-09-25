"""A single-use async host with atomic startup and cancellation-safe cleanup."""

import asyncio
from enum import Enum, auto
from typing import Self, cast

from bridge_agent.contracts.errors import (
    HostStateError,
    PluginActivationError,
    PluginProtocolError,
)
from bridge_agent.contracts.plugins import PreparedPlugin, ServiceIdentity, ServiceKey
from bridge_agent.kernel.context import OwnedContext
from bridge_agent.kernel.plan import activation_plan


class HostState(Enum):
    NEW = auto()
    STARTING = auto()
    RUNNING = auto()
    CLOSING = auto()
    CLOSED = auto()
    FAILED = auto()


class PluginHost:
    """Own all plugin instances and resources for exactly one application run."""

    def __init__(self, plugins: tuple[PreparedPlugin, ...]) -> None:
        self._plugins = plugins
        self._state = HostState.NEW
        self._services: dict[ServiceIdentity, object] = {}
        self._contexts: list[OwnedContext] = []
        self._cleanup_task: asyncio.Task[list[Exception]] | None = None

    @property
    def state(self) -> HostState:
        return self._state

    @property
    def registered_services(self) -> tuple[str, ...]:
        """Read-only diagnostic snapshot, never a mutable registration surface."""
        return tuple(key.name for key in self._services)

    def resolve[T](self, key: ServiceKey[T]) -> T:
        if self._state is not HostState.RUNNING:
            raise HostStateError(f"resolve requires RUNNING, got {self._state.name}")
        if key not in self._services:
            raise PluginProtocolError(f"Service unavailable: {key.name}")
        return cast(T, self._services[key])

    async def start(self) -> None:
        if self._state is not HostState.NEW:
            raise HostStateError(f"start requires NEW, got {self._state.name}")
        self._state = HostState.STARTING
        try:
            plan = activation_plan(self._plugins)
            instances = []
            # All constructors run before the first activation. Constructors and
            # config factories must remain free of resource acquisition.
            for prepared in plan:
                try:
                    instance = prepared.factory()
                except Exception as cause:
                    raise PluginActivationError(
                        f"Plugin {prepared.name}: construction failed"
                    ) from cause
                instances.append((prepared, instance))
            for prepared, instance in instances:
                context = OwnedContext(prepared, self._services)
                self._contexts.append(context)
                try:
                    await instance.activate(context)
                    context.publish()
                except Exception as cause:
                    raise PluginActivationError(
                        f"Plugin {prepared.name}: activation failed"
                    ) from cause
            self._state = HostState.RUNNING
        except BaseException as original:
            task = self._begin_cleanup(HostState.FAILED)
            errors, cancellation = await self._wait_cleanup(task)
            if isinstance(original, asyncio.CancelledError):
                if errors:
                    raise original from ExceptionGroup("Rollback failed", errors)
                raise
            if cancellation is not None:
                raise cancellation from BaseExceptionGroup(
                    "Startup interrupted during rollback", [original, *errors]
                )
            if errors:
                raise BaseExceptionGroup(
                    "Plugin startup and rollback failed", [original, *errors]
                ) from None
            raise

    def _begin_cleanup(self, final_state: HostState) -> asyncio.Task[list[Exception]]:
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(
                self._cleanup(final_state), name="bridge-agent-plugin-cleanup"
            )
        return self._cleanup_task

    async def _cleanup(self, final_state: HostState) -> list[Exception]:
        errors: list[Exception] = []
        try:
            for context in reversed(self._contexts):
                errors.extend(await context.close())
            return errors
        finally:
            self._contexts.clear()
            self._services.clear()
            self._state = final_state

    @staticmethod
    async def _wait_cleanup(
        task: asyncio.Task[list[Exception]],
    ) -> tuple[list[Exception], asyncio.CancelledError | None]:
        cancellation: asyncio.CancelledError | None = None
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as error:
                # Repeated cancellation of a waiter cannot abandon owned cleanup.
                cancellation = error
        return task.result(), cancellation

    async def close(self) -> None:
        if self._state is HostState.STARTING:
            raise HostStateError("Cancel the start task to interrupt STARTING")
        if self._state in (HostState.CLOSED, HostState.FAILED):
            return
        if self._state is HostState.NEW:
            self._state = HostState.CLOSED
            return
        self._state = HostState.CLOSING
        task = self._begin_cleanup(HostState.CLOSED)
        errors, cancellation = await self._wait_cleanup(task)
        if cancellation is not None:
            if errors:
                raise cancellation from ExceptionGroup("Plugin cleanup failed", errors)
            raise cancellation
        if errors:
            raise ExceptionGroup("Plugin cleanup failed", errors)

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        try:
            await self.close()
        except asyncio.CancelledError:
            raise
        except Exception as cleanup_error:
            if isinstance(exception, asyncio.CancelledError):
                raise exception from cleanup_error
            if exception is not None:
                raise BaseExceptionGroup(
                    "Application and cleanup failed", [exception, cleanup_error]
                ) from None
            raise
