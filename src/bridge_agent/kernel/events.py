"""Context-owned listeners with precise bail, filter, and disposal semantics."""

import asyncio
import inspect
from collections.abc import Callable
from dataclasses import dataclass

from bridge_agent.contracts.errors import PluginProtocolError

Listener = Callable[..., object]


@dataclass(eq=False)
class Hook[T]:
    owner: T
    callback: Listener
    global_: bool
    active: bool = True


class Events[T]:
    def __init__(self) -> None:
        self._listeners: dict[str, list[Hook[T]]] = {}

    def on(
        self,
        owner: T,
        name: str,
        callback: Listener,
        *,
        prepend: bool = False,
        once: bool = False,
        global_: bool = False,
    ) -> Callable[[], None]:
        hooks = self._listeners.setdefault(name, [])

        def wrapped(*args: object) -> object:
            if not hook.active:
                return None
            if once:
                dispose()
            return callback(*args)

        hook = Hook(owner, wrapped, global_)
        hooks.insert(0 if prepend else len(hooks), hook)

        def dispose() -> None:
            if not hook.active:
                return
            hook.active = False
            hooks.remove(hook)
            if not hooks:
                self._listeners.pop(name, None)

        return dispose

    def _callbacks(
        self, name: str, where: Callable[[T], bool] | None
    ) -> tuple[Listener, ...]:
        return tuple(
            hook.callback
            for hook in self._listeners.get(name, ())
            if hook.active and (hook.global_ or where is None or where(hook.owner))
        )

    def emit(
        self, name: str, args: tuple[object, ...], where: Callable[[T], bool] | None
    ) -> None:
        self._trace("emit", name, args)
        for callback in self._callbacks(name, where):
            synchronous(callback, *args)

    def bail(
        self, name: str, args: tuple[object, ...], where: Callable[[T], bool] | None
    ) -> object:
        self._trace("bail", name, args)
        for callback in self._callbacks(name, where):
            result = synchronous(callback, *args)
            if result is not None and result is not False:
                return result
        return None

    async def serial(
        self, name: str, args: tuple[object, ...], where: Callable[[T], bool] | None
    ) -> object:
        self._trace("serial", name, args)
        for callback in self._callbacks(name, where):
            result = await invoke(callback, *args)
            if result is not None and result is not False:
                return result
        return None

    async def parallel(
        self, name: str, args: tuple[object, ...], where: Callable[[T], bool] | None
    ) -> None:
        self._trace("parallel", name, args)
        results = await asyncio.gather(
            *(invoke(cb, *args) for cb in self._callbacks(name, where)),
            return_exceptions=True,
        )
        errors = [result for result in results if isinstance(result, BaseException)]
        if errors:
            raise BaseExceptionGroup("Event listeners failed", errors)

    def waterfall_sync(
        self,
        name: str,
        args: tuple[object, ...],
        next: Listener,
        where: Callable[[T], bool] | None = None,
    ) -> object:
        callbacks = self._callbacks(name, where)

        def step(index: int) -> object:
            if index == len(callbacks):
                return synchronous(next)
            return synchronous(callbacks[index], *args, lambda: step(index + 1))

        return step(0)

    def _trace(self, mode: str, name: str, args: tuple[object, ...]) -> None:
        if not name.startswith("internal."):
            self.emit("internal.dispatch", (mode, name, args), None)

    async def waterfall(
        self,
        name: str,
        args: tuple[object, ...],
        next: Listener,
        where: Callable[[T], bool] | None,
    ) -> object:
        self._trace("waterfall", name, args)
        callbacks = self._callbacks(name, where)

        async def step(index: int) -> object:
            if index == len(callbacks):
                return await invoke(next)
            return await invoke(callbacks[index], *args, lambda: step(index + 1))

        return await step(0)


def synchronous(callback: Listener, *args: object) -> object:
    result = callback(*args)
    if inspect.isawaitable(result):
        if inspect.iscoroutine(result):
            result.close()
        raise PluginProtocolError("Use parallel/serial for async listeners")
    return result


async def invoke(callback: Listener, *args: object) -> object:
    result = callback(*args)
    return await result if inspect.isawaitable(result) else result
