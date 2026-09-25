"""Dynamic service graph, separate from the strict single-use plugin host."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from contextlib import (
    AbstractAsyncContextManager,
    AbstractContextManager,
    asynccontextmanager,
)
from copy import deepcopy
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Self, cast

from bridge_agent.contracts.errors import (
    HostStateError,
    PluginActivationError,
    PluginProtocolError,
)
from bridge_agent.contracts.plugins import (
    PluginDefinition,
    PreparedPlugin,
    ServiceIdentity,
    ServiceKey,
)
from bridge_agent.kernel.context import ContextState, OwnedContext
from bridge_agent.kernel.events import Events, Listener, invoke


@dataclass(frozen=True)
class Accessor:
    get: Callable[[], object]


@dataclass(frozen=True)
class Binding:
    value: object
    check: Callable[[], bool]


@dataclass(frozen=True)
class PluginStatus:
    instance_id: str
    plugin: str
    state: str
    missing: tuple[str, ...] = ()
    effects: tuple[str, ...] = ()


class ConfigView(Mapping[str, object]):
    def __init__(self, data: Mapping[str, object]) -> None:
        self._data = deepcopy(dict(data))

    def __getitem__(self, key: str) -> object:
        return deepcopy(self._data[key])

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


def ordinary_config(
    config: Mapping[str, object], fields: tuple[str, ...]
) -> dict[str, object]:
    result = deepcopy(dict(config))
    for path in fields:
        parts = path.split(".")
        current = result
        for part in parts[:-1]:
            value = current.get(part)
            if not isinstance(value, dict):
                break
            current = value
        else:
            current.pop(parts[-1], None)
    return result


class DynamicResources(OwnedContext):
    def __init__(
        self, plugin: PreparedPlugin, services: dict[ServiceIdentity, object]
    ) -> None:
        super().__init__(plugin, services)
        self.effects: dict[object, str] = {}
        self.config = ConfigView({})

    def _assert_activating(self) -> None:
        if self.state not in (ContextState.ACTIVATING, ContextState.ACTIVE):
            raise PluginProtocolError(
                "Cannot register an effect on an inactive Context"
            )


@dataclass
class Instance:
    id: str
    definition: PluginDefinition
    prepared: PreparedPlugin
    scope: "Context"
    config: dict[str, object] = field(default_factory=dict)
    state: str = "pending"
    owned: DynamicResources | None = None
    values: dict[ServiceIdentity, object] = field(default_factory=dict)


class Context:
    def __init__(
        self,
        host: "DynamicHost",
        owned: DynamicResources | None = None,
        labels: Mapping[ServiceIdentity, object] | None = None,
    ) -> None:
        self._host = host
        self._owned = owned
        self._labels = dict(labels or {})
        self._metadata: dict[str, object] = {}
        self._filter: Callable[[Context], bool] | None = None
        self._intercepts: dict[ServiceIdentity, dict[str, object]] = {}

    def _copy(self, owned: DynamicResources | None = None) -> "Context":
        result = Context(self._host, owned or self._owned, self._labels)
        result._filter = self._filter
        result._metadata = deepcopy(self._metadata)
        result._intercepts = {
            key: deepcopy(value) for key, value in self._intercepts.items()
        }
        return result

    @property
    def metadata(self) -> Mapping[str, object]:
        return MappingProxyType(deepcopy(self._metadata))

    def extend(self, metadata: Mapping[str, object]) -> "Context":
        result = self._copy()
        result._metadata.update(deepcopy(dict(metadata)))
        return result

    def intercept(
        self, key: ServiceIdentity, config: Mapping[str, object]
    ) -> "Context":
        result = self._copy()
        result._intercepts[key] = {
            **result._intercepts.get(key, {}),
            **deepcopy(dict(config)),
        }
        return result

    def config_for(
        self,
        key: ServiceIdentity,
        base: Mapping[str, object] | None = None,
        head: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        return deepcopy(
            {**(base or {}), **self._intercepts.get(key, {}), **(head or {})}
        )

    def isolate(self, key: ServiceIdentity, label: object | None = None) -> "Context":
        result = self._copy()
        result._labels[key] = object() if label is None else label
        hash(result._labels[key])
        return result

    def _label(self, key: ServiceIdentity) -> object:
        return self._labels.get(key)

    @property
    def root(self) -> "Context":
        return self._host.context

    def require[T](self, key: ServiceKey[T]) -> T:
        if self._owned is not None:
            self._owned.require(key)
        return cast(
            T,
            self._host._events.waterfall_sync(
                "internal.get", (self, key), lambda: self._host._resolve(key, self)
            ),
        )

    def select(self, predicate: Callable[["Context"], bool]) -> "Context":
        result = self._copy()
        result._filter = predicate
        return result

    def on(
        self,
        name: str,
        callback: Listener,
        *,
        prepend: bool = False,
        once: bool = False,
        global_: bool = False,
    ) -> Callable[[], None]:
        if name != "internal.listener":
            intercepted = self._host._events.bail(
                "internal.listener", (self, name, callback), self._filter
            )
            if intercepted is not None and intercepted is not False:
                if not callable(intercepted):
                    raise PluginProtocolError("Listener hook must return a disposer")
                released = False

                def dispose_override() -> None:
                    nonlocal released
                    if not released:
                        released = True
                        intercepted()

                self.on_close(dispose_override)
                return dispose_override
        dispose = self._host._events.on(
            self, name, callback, prepend=prepend, once=once, global_=global_
        )
        try:
            self.on_close(dispose)
        except BaseException:
            dispose()
            raise
        return dispose

    def once(
        self,
        name: str,
        callback: Listener,
        *,
        prepend: bool = False,
        global_: bool = False,
    ) -> Callable[[], None]:
        return self.on(name, callback, prepend=prepend, once=True, global_=global_)

    def emit(self, name: str, *args: object) -> None:
        self._host._events.emit(name, args, self._filter)

    def bail(self, name: str, *args: object) -> object:
        return self._host._events.bail(name, args, self._filter)

    async def serial(self, name: str, *args: object) -> object:
        return await self._host._events.serial(name, args, self._filter)

    async def parallel(self, name: str, *args: object) -> None:
        await self._host._events.parallel(name, args, self._filter)

    async def waterfall(self, name: str, *args: object, next: Listener) -> object:
        return await self._host._events.waterfall(name, args, next, self._filter)

    async def effect(
        self, execute: Listener, *, label: str = "resource"
    ) -> Callable[[], Awaitable[None]]:
        owner = self._owner()
        if owner.state not in (ContextState.ACTIVATING, ContextState.ACTIVE):
            raise PluginProtocolError("Cannot create effect on inactive Context")
        cleanup = await invoke(execute)
        if not callable(cleanup):
            raise PluginProtocolError("Effect must return a disposer")
        token = object()
        owner.effects[token] = label

        task: asyncio.Task[object] | None = None

        async def dispose() -> None:
            nonlocal task
            if task is None:
                task = asyncio.create_task(invoke(cleanup))
            cancelled: asyncio.CancelledError | None = None
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError as error:
                    cancelled = error
            owner.effects.pop(token, None)
            task.result()
            if cancelled:
                raise cancelled

        try:
            self.on_close_async(dispose)
        except BaseException:
            await dispose()
            raise
        return dispose

    def logger(self, name: str) -> logging.Logger:
        return logging.getLogger("bridge_agent.plugins." + name)

    def export_logs(self, logger: logging.Logger, handler: logging.Handler) -> None:
        def dispose() -> None:
            logger.removeHandler(handler)
            handler.close()

        self.on_close(dispose)
        logger.addHandler(handler)

    @property
    def config(self) -> Mapping[str, object]:
        return self._owner().config

    def _owner(self) -> DynamicResources:
        if self._owned is None:
            raise PluginProtocolError(
                "Resource registration requires a plugin-owned Context"
            )
        return self._owned

    def provide[T](
        self, key: ServiceKey[T], value: T, *, check: Callable[[], bool] | None = None
    ) -> None:
        owner = self._owner()
        if owner.state is not ContextState.ACTIVATING:
            raise PluginProtocolError("Service publication requires activation")
        self._host._events.waterfall_sync(
            "internal.set",
            (self, key, value),
            lambda: owner.provide(
                cast(ServiceKey[object], key), Binding(value, check) if check else value
            ),
        )

    def accessor[T](self, key: ServiceKey[T], getter: Callable[[], T]) -> None:
        self.provide(cast(ServiceKey[object], key), Accessor(getter))

    def alias[T](self, alias: ServiceKey[T], target: ServiceKey[T]) -> None:
        self.accessor(alias, lambda: self.require(target))

    def on_close(self, callback: Callable[[], None]) -> None:
        self._owner().on_close(callback)

    def on_close_async(self, callback: Callable[[], Awaitable[None]]) -> None:
        self._owner().on_close_async(callback)

    def enter_context[T](self, manager: AbstractContextManager[T]) -> T:
        return self._owner().enter_context(manager)

    async def enter_async_context[T](
        self, manager: AbstractAsyncContextManager[T]
    ) -> T:
        return await self._owner().enter_async_context(manager)


class DynamicHost:
    def __init__(self, *, drain_timeout: float = 30) -> None:
        self._events: Events[Context] = Events()
        self.context = Context(self)
        self._instances: dict[str, Instance] = {}
        self._closed = False
        self._gate = asyncio.Lock()
        self._mutator: asyncio.Task[object] | None = None
        self._idle = asyncio.Event()
        self._idle.set()
        self._leases = 0
        self._drain_timeout = drain_timeout
        self._order: list[str] = []

    @asynccontextmanager
    async def lease(self, context: Context | None = None) -> AsyncIterator[Context]:
        async with self._gate:
            if self._closed:
                raise HostStateError("Dynamic host is closed")
            self._leases += 1
            self._idle.clear()
        try:
            yield context or self.context
        finally:
            self._leases -= 1
            if not self._leases:
                self._idle.set()

    @asynccontextmanager
    async def _mutation(self, *, allow_closed: bool = False) -> AsyncIterator[None]:
        if self._closed and not allow_closed:
            raise HostStateError("Dynamic host is closed")
        if self._mutator is asyncio.current_task():
            yield
            return
        async with self._gate:
            if self._closed and not allow_closed:
                raise HostStateError("Dynamic host is closed")
            try:
                async with asyncio.timeout(self._drain_timeout):
                    await self._idle.wait()
            except TimeoutError:
                raise HostStateError(
                    "Timed out waiting for in-flight calls; no resources released"
                ) from None
            self._mutator = asyncio.current_task()
            try:
                yield
            finally:
                self._mutator = None

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        """Serialize a loader update and hold off calls across all entry changes."""
        async with self._mutation():
            yield

    async def _config(
        self, instance_id: str, config: Mapping[str, object]
    ) -> dict[str, object]:
        value = await self.context.waterfall(
            "internal.config", instance_id, dict(config), next=lambda: dict(config)
        )
        if not isinstance(value, dict) or any(
            not isinstance(key, str) for key in value
        ):
            raise PluginProtocolError("Config hook must return a string-keyed mapping")
        return value

    def _notify(self, name: str, *args: object) -> None:
        try:
            self.context.emit(name, *args)
        except Exception:
            logging.getLogger(__name__).warning("Diagnostic listener failed: %s", name)

    def _set_state(self, instance: Instance, state: str) -> None:
        previous = instance.state
        instance.state = state
        self._notify("internal.status", self.status(instance.id), previous)

    def _provider(self, key: ServiceIdentity, scope: Context) -> Instance:
        for instance in self._instances.values():
            if (
                instance.state == "active"
                and key in instance.definition.provides
                and instance.scope._label(key) == scope._label(key)
            ):
                value = instance.values[key]
                if isinstance(value, Binding) and not value.check():
                    continue
                return instance
        raise PluginProtocolError(f"Service unavailable: {key.name}")

    def _resolve(self, key: ServiceIdentity, scope: Context) -> object:
        value = self._provider(key, scope).values[key]
        if isinstance(value, Binding):
            value = value.value
        return value.get() if isinstance(value, Accessor) else value

    @property
    def instances(self) -> tuple[PluginStatus, ...]:
        return tuple(self.status(ident) for ident in self._instances)

    def definition(self, instance_id: str) -> PluginDefinition:
        return self._instances[instance_id].definition

    def status(self, instance_id: str) -> PluginStatus:
        instance = self._instances[instance_id]
        missing = []
        for key in instance.definition.requires:
            try:
                self._provider(key, instance.scope)
            except PluginProtocolError:
                missing.append(key.name)
        return PluginStatus(
            instance.id,
            instance.definition.name,
            instance.state,
            tuple(missing),
            tuple(instance.owned.effects.values()) if instance.owned else (),
        )

    def _check_providers(
        self, instance_id: str, definition: PluginDefinition, scope: Context
    ) -> None:
        for instance in self._instances.values():
            if (
                instance.id != instance_id
                and instance.state != "disposed"
                and any(
                    key in instance.definition.provides
                    and instance.scope._label(key) == scope._label(key)
                    for key in definition.provides
                )
            ):
                raise PluginProtocolError("Duplicate scoped provider")

    async def mount(
        self,
        instance_id: str,
        definition: PluginDefinition,
        config: Mapping[str, object] | None = None,
        *,
        context: Context | None = None,
    ) -> None:
        async with self._mutation():
            if self._closed:
                raise HostStateError("Dynamic host is closed")
            if (
                instance_id in self._instances
                and self._instances[instance_id].state != "disposed"
            ):
                raise PluginProtocolError("Duplicate instance ID")
            scope = context or self.context
            if scope._host is not self:
                raise PluginProtocolError("Context belongs to another host")
            self._check_providers(instance_id, definition, scope)
            config = await self._config(instance_id, config or {})
            config = (
                dict(definition.validate_config(config or {}))
                if definition.validate_config
                else dict(config or {})
            )
            prepared = PreparedPlugin(
                definition.name,
                definition.prepare(config or {}),
                definition.requires,
                definition.provides,
            )
            self._instances[instance_id] = Instance(
                instance_id,
                definition,
                prepared,
                context or self.context,
                deepcopy(dict(config or {})),
            )
            self._notify("internal.plugin", self.status(instance_id))
            await self._reconcile()

    async def _reconcile(self) -> None:
        changed = True
        while changed:
            changed = False
            for instance in self._instances.values():
                if instance.state != "pending" or self.status(instance.id).missing:
                    continue
                values = {
                    key: self._resolve(key, instance.scope)
                    for key in instance.definition.requires
                }
                owned = DynamicResources(instance.prepared, values)
                owned.config = ConfigView(instance.config)
                instance.owned = owned
                instance.values = values
                self._set_state(instance, "loading")
                try:
                    await instance.prepared.factory().activate(
                        instance.scope._copy(owned)
                    )
                    owned.publish()
                except BaseException as error:
                    self._set_state(instance, "failed")
                    errors = await owned.close()
                    if isinstance(error, asyncio.CancelledError):
                        if errors:
                            raise error from ExceptionGroup("Rollback failed", errors)
                        raise
                    if errors:
                        raise BaseExceptionGroup(
                            "Activation and cleanup failed", [error, *errors]
                        ) from None
                    raise PluginActivationError(
                        f"Plugin {instance.id}: activation failed"
                    ) from error
                self._set_state(instance, "active")
                self._order.append(instance.id)
                changed = True

    async def refresh(self) -> None:
        async with self._mutation():
            unavailable = {
                instance.id
                for instance in self._instances.values()
                if instance.state == "active" and self.status(instance.id).missing
            }
            errors = await self._stop(self._dependents(unavailable))
            if errors:
                raise ExceptionGroup("Dependency refresh cleanup failed", errors)
            await self._reconcile()

    async def set_service[T](
        self, instance_id: str, key: ServiceKey[T], value: T
    ) -> None:
        async with self._mutation():
            instance = self._instances[instance_id]
            if instance.state != "active" or key not in instance.definition.provides:
                raise PluginProtocolError(
                    "Service replacement requires its active provider"
                )
            errors = await self._stop(self._dependents({instance_id}) - {instance_id})
            if errors:
                raise ExceptionGroup("Service replacement cleanup failed", errors)
            previous = instance.values[key]
            instance.values[key] = (
                Binding(value, previous.check)
                if isinstance(previous, Binding)
                else value
            )
            self._notify("internal.service", instance_id, key.name)
            await self._reconcile()

    async def reconfigure(
        self,
        instance_id: str,
        config: Mapping[str, object],
        *,
        definition: PluginDefinition | None = None,
    ) -> bool:
        async with self._mutation():
            applied = False

            async def apply() -> None:
                nonlocal applied
                await self._replace(instance_id, config, definition)
                applied = True

            await self.context.waterfall(
                "internal.update", instance_id, dict(config), next=apply
            )
            return applied

    async def _replace(
        self,
        instance_id: str,
        config: Mapping[str, object],
        definition: PluginDefinition | None,
    ) -> None:
        instance = self._instances[instance_id]
        definition = definition or instance.definition
        self._check_providers(instance_id, definition, instance.scope)
        config = await self._config(instance_id, config)
        config = (
            dict(definition.validate_config(config))
            if definition.validate_config
            else dict(config)
        )
        candidate = PreparedPlugin(
            definition.name,
            definition.prepare(config),
            definition.requires,
            definition.provides,
        )
        if (
            definition is instance.definition
            and definition.volatile_fields
            and instance.state == "active"
            and ordinary_config(config, definition.volatile_fields)
            == ordinary_config(instance.config, definition.volatile_fields)
        ):
            instance.config = deepcopy(config)
            assert instance.owned is not None
            instance.owned.config._data = deepcopy(config)
            self._notify(
                "loader.volatile-update", instance_id, definition.volatile_fields
            )
            return
        errors = await self._stop(self._dependents({instance_id}))
        if errors:
            raise ExceptionGroup("Replacement cleanup failed", errors)
        previous = instance.prepared
        previous_definition = instance.definition
        previous_config = instance.config
        instance.definition = definition
        instance.config = deepcopy(dict(config))
        instance.prepared = candidate
        self._set_state(instance, "pending")
        try:
            await self._reconcile()
        except BaseException as error:
            affected = self._dependents({instance_id})
            cleanup = await self._stop(affected)
            for ident in affected:
                if self._instances[ident].state == "failed":
                    self._set_state(self._instances[ident], "pending")
            instance.prepared = previous
            instance.definition = previous_definition
            instance.config = previous_config
            self._set_state(instance, "pending")
            try:
                await self._reconcile()
            except BaseException as rollback:
                raise BaseExceptionGroup(
                    "Replacement and rollback failed", [error, rollback, *cleanup]
                ) from None
            if cleanup:
                raise BaseExceptionGroup(
                    "Replacement and cleanup failed", [error, *cleanup]
                ) from None
            raise

    async def reload(
        self, instance_id: str, definition: PluginDefinition | None = None
    ) -> bool:
        return await self.reconfigure(
            instance_id, self._instances[instance_id].config, definition=definition
        )

    def _dependents(self, ids: set[str]) -> set[str]:
        result = set(ids)
        while True:
            previous = len(result)
            slots = {
                (key, self._instances[ident].scope._label(key))
                for ident in result
                for key in self._instances[ident].definition.provides
            }
            for instance in self._instances.values():
                if instance.state != "disposed" and any(
                    (key, instance.scope._label(key)) in slots
                    for key in instance.definition.requires
                ):
                    result.add(instance.id)
            if len(result) == previous:
                return result

    async def _stop(self, ids: set[str]) -> list[Exception]:
        task = asyncio.create_task(self._stop_impl(ids))
        cancelled: asyncio.CancelledError | None = None
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as error:
                cancelled = error
        errors = task.result()
        if cancelled is not None:
            if errors:
                raise cancelled from ExceptionGroup("Cleanup failed", errors)
            raise cancelled
        return errors

    async def _stop_impl(self, ids: set[str]) -> list[Exception]:
        errors: list[Exception] = []
        for ident in reversed(tuple(self._order)):
            if ident not in ids:
                continue
            instance = self._instances[ident]
            self._set_state(instance, "unloading")
            if instance.owned is not None:
                errors.extend(await instance.owned.close())
            instance.owned = None
            instance.values.clear()
            self._set_state(instance, "pending")
            self._order.remove(ident)
        return errors

    async def unmount(self, instance_id: str) -> None:
        async with self._mutation():
            errors = await self._stop(self._dependents({instance_id}))
            self._set_state(self._instances[instance_id], "disposed")
            self._notify("internal.plugin", self.status(instance_id))
            if errors:
                raise ExceptionGroup("Dynamic cleanup failed", errors)

    async def close(self) -> None:
        async with self._mutation(allow_closed=True):
            if self._closed:
                return
            self._closed = True
            try:
                errors = await self._stop(set(self._instances))
            finally:
                for instance in self._instances.values():
                    self._set_state(instance, "disposed")
            if errors:
                raise ExceptionGroup("Dynamic cleanup failed", errors)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
