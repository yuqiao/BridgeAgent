"""Declarations shared by plugins, composition, and the host."""

from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar


@dataclass(frozen=True, eq=False)
class ServiceIdentity:
    """Untyped identity used only in declarations and registry storage."""

    name: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Service names must not be empty")


T = TypeVar("T")


# PEP 695 would infer covariance for this token with no T-valued members.
# Explicit invariance prevents provide() from widening T to accept wrong values.
class ServiceKey(ServiceIdentity, Generic[T]):  # noqa: UP046
    """Canonical invariant token: registration must preserve its exact value type."""


class PluginContext(Protocol):
    """A plugin's declared dependencies, contributions, and startup resources."""

    def require[T](self, key: ServiceKey[T]) -> T:
        """Read a declared dependency while this context is alive."""
        ...

    def provide[T](self, key: ServiceKey[T], value: T) -> None:
        """Stage a declared service during activation."""
        ...

    def on_close(self, callback: Callable[[], None]) -> None:
        """Own a synchronous cleanup callback registered during activation."""
        ...

    def on_close_async(self, callback: Callable[[], Awaitable[None]]) -> None:
        """Own an asynchronous cleanup callback registered during activation."""
        ...

    def enter_context[T](self, manager: AbstractContextManager[T]) -> T:
        """Acquire a resource and register its exit after successful entry."""
        ...

    async def enter_async_context[T](
        self, manager: AbstractAsyncContextManager[T]
    ) -> T:
        """Acquire an async resource and own its exit after successful entry."""
        ...


class Plugin(Protocol):
    """A configured instance whose constructor must not acquire resources."""

    async def activate(self, context: PluginContext) -> None:
        """Publish declarations and acquire resources through the context."""
        ...


@dataclass(frozen=True)
class PluginDefinition:
    """Static declarations and a pure configuration-to-factory boundary."""

    name: str
    prepare: Callable[[Mapping[str, object]], Callable[[], Plugin]]
    requires: tuple[ServiceIdentity, ...] = ()
    provides: tuple[ServiceIdentity, ...] = ()
    volatile_fields: tuple[str, ...] = ()
    validate_config: Callable[[Mapping[str, object]], Mapping[str, object]] | None = (
        None
    )


@dataclass(frozen=True)
class PreparedPlugin:
    """Validated configuration bound to a side-effect-free instance factory."""

    name: str
    factory: Callable[[], Plugin]
    requires: tuple[ServiceIdentity, ...] = ()
    provides: tuple[ServiceIdentity, ...] = ()
