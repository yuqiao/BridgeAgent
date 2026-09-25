"""Compose the dynamic host without changing Agent application use cases."""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

from bridge_agent.bootstrap.config import PluginCatalog
from bridge_agent.bootstrap.dynamic import DynamicLoader
from bridge_agent.bootstrap.reload import SourceReloader
from bridge_agent.contracts.agent import AGENT_RUNTIME
from bridge_agent.contracts.plugins import ServiceKey
from bridge_agent.contracts.sessions import SESSION_CONTROL
from bridge_agent.kernel.dynamic import DynamicHost, PluginStatus
from bridge_agent.plugins.dynamic_runtime import DynamicAgentRuntime


class RunningDynamic:
    def __init__(
        self,
        host: DynamicHost,
        loader: DynamicLoader,
        path: Path,
        entry: str | None = None,
    ) -> None:
        self.host = host
        self.loader = loader
        self.path = path
        self.runtime = DynamicAgentRuntime(
            host, lambda: loader.context(entry) if entry else host.context
        )

    def resolve[T](self, key: ServiceKey[T]) -> T:
        if key is AGENT_RUNTIME or key is SESSION_CONTROL:
            return cast(T, self.runtime)
        return self.host.context.require(key)

    @property
    def instances(self) -> tuple[PluginStatus, ...]:
        return self.host.instances

    async def reload(self) -> None:
        await self.loader.load(self.path)


@asynccontextmanager
async def open_dynamic(
    catalog: PluginCatalog,
    path: Path,
    *,
    entry: str | None = None,
    watch: bool = False,
    on_reload: Callable[[tuple[str, ...]], None] | None = None,
) -> AsyncIterator[RunningDynamic]:
    async with DynamicHost() as host:
        loader = DynamicLoader(host, catalog)
        await loader.load(path)
        reloader = SourceReloader(host)
        reloader.register_installed()
        reloader.watch_config(path, loader)
        stop = asyncio.Event()
        async with asyncio.TaskGroup() as group:
            if watch:
                group.create_task(reloader.watch(stop, on_reload=on_reload))
            try:
                yield RunningDynamic(host, loader, path, entry)
            finally:
                stop.set()
