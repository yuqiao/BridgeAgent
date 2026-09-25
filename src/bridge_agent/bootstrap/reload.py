"""Reload an explicit Python dependency manifest after draining active calls."""

import asyncio
import hashlib
import importlib
import importlib.metadata
import logging
import sys
import types
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from bridge_agent.bootstrap.dynamic import DynamicLoader
from bridge_agent.contracts.errors import ConfigurationError, RestartRequired
from bridge_agent.contracts.packages import PluginExport
from bridge_agent.contracts.plugins import ServiceIdentity
from bridge_agent.kernel.dynamic import DynamicHost


@dataclass
class ModuleSource:
    path: Path
    digest: bytes


@dataclass
class Source:
    module: str
    attribute: str
    modules: tuple[str, ...]
    extra_requires: tuple[ServiceIdentity, ...] = ()


class SourceReloader:
    def __init__(self, host: DynamicHost) -> None:
        self.host = host
        self._sources: dict[str, Source] = {}
        self._files: dict[Path, bytes] = {}
        self._configs: dict[Path, tuple[bytes, DynamicLoader]] = {}
        self._modules: dict[str, ModuleSource] = {}
        self._restart: set[str] = set()
        self._installed_ids: set[str] = set()
        for name, module in tuple(sys.modules.items()):
            if (
                name.startswith("bridge_agent.")
                and getattr(module, "__file__", None)
                and str(module.__file__).endswith(".py")
            ):
                self.require_restart(name)

    def register_installed(self) -> None:
        entries = tuple(importlib.metadata.entry_points(group="bridge_agent.plugins"))
        active: set[str] = set()
        for status in self.host.instances:
            if status.state == "disposed":
                continue
            candidates = [entry for entry in entries if entry.name == status.plugin]
            if not candidates:
                continue
            if len(candidates) != 1:
                raise ConfigurationError("Ambiguous reload entry point")
            entry = candidates[0]
            export = entry.load()
            if not isinstance(export, PluginExport):
                raise ConfigurationError("Invalid reload export")
            self.register(
                status.instance_id,
                entry.module,
                entry.attr or "plugin",
                dependencies=export.reload_modules,
            )
            for module in export.restart_modules:
                self.require_restart(module)
            active.add(status.instance_id)
        for ident in self._installed_ids - active:
            self._sources.pop(ident, None)
        self._installed_ids = active

    def require_restart(self, name: str) -> None:
        module = importlib.import_module(name)
        if module.__file__ is None:
            raise ConfigurationError("Restart watch requires a source file")
        path = Path(module.__file__).resolve()
        self._modules.setdefault(
            name, ModuleSource(path, hashlib.sha256(path.read_bytes()).digest())
        )
        self._restart.add(name)

    def register(
        self,
        instance_id: str,
        module: str,
        attribute: str,
        *,
        dependencies: tuple[str, ...] = (),
    ) -> None:
        modules = tuple(dict.fromkeys((*dependencies, module)))
        for name in modules:
            loaded = importlib.import_module(name)
            if loaded.__file__ is None:
                raise ConfigurationError("Reload requires a Python source file")
            path = Path(loaded.__file__).resolve()
            if path.suffix != ".py":
                raise ConfigurationError("Reload requires a Python source file")
            self._modules.setdefault(
                name, ModuleSource(path, hashlib.sha256(path.read_bytes()).digest())
            )
        export = getattr(importlib.import_module(module), attribute)
        if not isinstance(export, PluginExport):
            raise ConfigurationError("Invalid source export")
        extra = tuple(
            key
            for key in self.host.definition(instance_id).requires
            if key not in export.definition.requires
        )
        self._sources[instance_id] = Source(module, attribute, modules, extra)

    def watch_file(self, path: Path) -> None:
        path = path.resolve()
        self._files[path] = hashlib.sha256(path.read_bytes()).digest()

    def watch_config(self, path: Path, loader: DynamicLoader) -> None:
        path = path.resolve()
        self._configs[path] = (hashlib.sha256(path.read_bytes()).digest(), loader)

    async def watch(
        self,
        stop: asyncio.Event,
        *,
        interval: float = 0.25,
        on_reload: Callable[[tuple[str, ...]], None] | None = None,
    ) -> None:
        if interval <= 0:
            raise ValueError("Watch interval must be positive")
        while not stop.is_set():
            try:
                changed = await self.check()
                if changed and on_reload:
                    on_reload(changed)
            except RestartRequired:
                raise
            except Exception:
                logging.getLogger(__name__).warning(
                    "Reload failed; inspect configuration or plugin source"
                )
            try:
                await asyncio.wait_for(stop.wait(), interval)
            except TimeoutError:
                pass

    async def check(self) -> tuple[str, ...]:
        async with self.host.transaction():
            file_changes: list[str] = []
            for path, previous in tuple(self._files.items()):
                digest = (
                    hashlib.sha256(path.read_bytes()).digest()
                    if path.exists()
                    else b"missing"
                )
                if digest != previous:
                    file_changes.append(str(path))
                    self._files[path] = digest
            if file_changes:
                self.host._notify("hmr.change", tuple(file_changes))
            config_changes: list[str] = []
            for path, (previous, loader) in tuple(self._configs.items()):
                digest = hashlib.sha256(path.read_bytes()).digest()
                if digest != previous:
                    self._configs[path] = (digest, loader)
                    await loader.load(path)
                    self.register_installed()
                    config_changes.append(str(path))
            data = {
                name: module.path.read_bytes() for name, module in self._modules.items()
            }
            changed = {
                name
                for name, content in data.items()
                if hashlib.sha256(content).digest() != self._modules[name].digest
            }
            if changed.intersection(self._restart):
                raise RestartRequired(
                    "Framework or shared contracts changed; restart the process"
                )
            affected = [
                ident
                for ident, source in self._sources.items()
                if changed.intersection(source.modules)
                and self.host.status(ident).state != "disposed"
            ]
            names = tuple(
                dict.fromkeys(
                    name for ident in affected for name in self._sources[ident].modules
                )
            )
            old_modules = {name: sys.modules[name] for name in names}
            old_definitions = {ident: self.host.definition(ident) for ident in affected}
            parent_attributes: list[tuple[types.ModuleType, str, object, bool]] = []
            replaced: list[str] = []
            try:
                for name in names:
                    old = old_modules[name]
                    fresh = types.ModuleType(name)
                    fresh.__file__ = str(self._modules[name].path)
                    fresh.__package__ = old.__package__
                    fresh.__spec__ = old.__spec__
                    if hasattr(old, "__path__"):
                        fresh.__path__ = old.__path__
                    sys.modules[name] = fresh
                    parent_name, _, short_name = name.rpartition(".")
                    parent = sys.modules.get(parent_name)
                    if parent is not None:
                        parent_attributes.append(
                            (
                                parent,
                                short_name,
                                getattr(parent, short_name, None),
                                hasattr(parent, short_name),
                            )
                        )
                        setattr(parent, short_name, fresh)
                    exec(compile(data[name], fresh.__file__, "exec"), fresh.__dict__)
                for ident in affected:
                    source = self._sources[ident]
                    export = getattr(sys.modules[source.module], source.attribute)
                    if (
                        not isinstance(export, PluginExport)
                        or export.api_version != 1
                        or export.definition.name != old_definitions[ident].name
                    ):
                        raise ConfigurationError(
                            "Reloaded plugin export is incompatible"
                        )
                    definition = replace(
                        export.definition,
                        requires=tuple(
                            dict.fromkeys(
                                (*export.definition.requires, *source.extra_requires)
                            )
                        ),
                    )
                    if not await self.host.reload(ident, definition):
                        raise ConfigurationError("Source update was vetoed")
                    replaced.append(ident)
            except BaseException as error:
                sys.modules.update(old_modules)
                for parent, short_name, value, existed in reversed(parent_attributes):
                    if existed:
                        setattr(parent, short_name, value)
                    else:
                        delattr(parent, short_name)
                failures: list[BaseException] = [error]
                for ident in reversed(replaced):
                    try:
                        await self.host.reload(ident, old_definitions[ident])
                    except BaseException as rollback:
                        failures.append(rollback)
                if len(failures) > 1:
                    raise BaseExceptionGroup(
                        "Source replacement and rollback failed", failures
                    ) from None
                raise
            finally:
                for name in changed:
                    self._modules[name].digest = hashlib.sha256(data[name]).digest()
            result = tuple((*config_changes, *affected))
            if result:
                self.host._notify("hmr.reload", result)
            return result
