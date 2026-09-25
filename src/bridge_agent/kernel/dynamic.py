"""Dynamic service graph, separate from the strict single-use plugin host."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager, AbstractContextManager
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
from bridge_agent.kernel.context import OwnedContext


@dataclass(frozen=True)
class PluginStatus:
    instance_id: str
    plugin: str
    state: str
    missing: tuple[str, ...] = ()


@dataclass
class Instance:
    id: str
    definition: PluginDefinition
    prepared: PreparedPlugin
    scope: "Context"
    state: str = "pending"
    owned: OwnedContext | None = None
    values: dict[ServiceIdentity, object] = field(default_factory=dict)


class Context:
    def __init__(
        self,
        host: "DynamicHost",
        owned: OwnedContext | None = None,
        labels: Mapping[ServiceIdentity, object] | None = None,
    ) -> None:
        self._host = host
        self._owned = owned
        self._labels = dict(labels or {})
        self._metadata: dict[str, object] = {}
        self._intercepts: dict[ServiceIdentity, dict[str, object]] = {}

    def _copy(self, owned: OwnedContext | None = None) -> "Context":
        result = Context(self._host, owned or self._owned, self._labels)
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
            return self._owned.require(key)
        return cast(T, self._host._resolve(key, self))

    def _owner(self) -> OwnedContext:
        if self._owned is None:
            raise PluginProtocolError(
                "Resource registration requires a plugin-owned Context"
            )
        return self._owned

    def provide[T](self, key: ServiceKey[T], value: T) -> None:
        self._owner().provide(key, value)

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
    def __init__(self) -> None:
        self.context = Context(self)
        self._instances: dict[str, Instance] = {}
        self._closed = False
        self._order: list[str] = []

    def _resolve(self, key: ServiceIdentity, scope: Context) -> object:
        for instance in self._instances.values():
            if (
                instance.state == "active"
                and key in instance.definition.provides
                and instance.scope._label(key) == scope._label(key)
            ):
                return instance.values[key]
        raise PluginProtocolError(f"Service unavailable: {key.name}")

    def status(self, instance_id: str) -> PluginStatus:
        instance = self._instances[instance_id]
        missing = []
        for key in instance.definition.requires:
            try:
                self._resolve(key, instance.scope)
            except PluginProtocolError:
                missing.append(key.name)
        return PluginStatus(
            instance.id, instance.definition.name, instance.state, tuple(missing)
        )

    async def mount(
        self,
        instance_id: str,
        definition: PluginDefinition,
        config: Mapping[str, object] | None = None,
        *,
        context: Context | None = None,
    ) -> None:
        if self._closed:
            raise HostStateError("Dynamic host is closed")
        if instance_id in self._instances:
            raise PluginProtocolError("Duplicate instance ID")
        scope = context or self.context
        if scope._host is not self:
            raise PluginProtocolError("Context belongs to another host")
        for instance in self._instances.values():
            if instance.state != "disposed" and any(
                key in instance.definition.provides
                and instance.scope._label(key) == scope._label(key)
                for key in definition.provides
            ):
                raise PluginProtocolError("Duplicate scoped provider")
        prepared = PreparedPlugin(
            definition.name,
            definition.prepare(config or {}),
            definition.requires,
            definition.provides,
        )
        self._instances[instance_id] = Instance(
            instance_id, definition, prepared, context or self.context
        )
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
                owned = OwnedContext(instance.prepared, values)
                instance.owned = owned
                instance.values = values
                instance.state = "loading"
                try:
                    await instance.prepared.factory().activate(
                        instance.scope._copy(owned)
                    )
                    owned.publish()
                except BaseException as error:
                    instance.state = "failed"
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
                instance.state = "active"
                self._order.append(instance.id)
                changed = True

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
        errors: list[Exception] = []
        for ident in reversed(tuple(self._order)):
            if ident not in ids:
                continue
            instance = self._instances[ident]
            instance.state = "unloading"
            if instance.owned is not None:
                errors.extend(await instance.owned.close())
            instance.owned = None
            instance.values.clear()
            instance.state = "pending"
            self._order.remove(ident)
        return errors

    async def unmount(self, instance_id: str) -> None:
        errors = await self._stop(self._dependents({instance_id}))
        self._instances[instance_id].state = "disposed"
        if errors:
            raise ExceptionGroup("Dynamic cleanup failed", errors)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        errors = await self._stop(set(self._instances))
        for instance in self._instances.values():
            instance.state = "disposed"
        if errors:
            raise ExceptionGroup("Dynamic cleanup failed", errors)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
