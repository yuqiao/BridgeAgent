"""Pure-data dynamic entry tree; groups own their descendant entries."""

import os
import tempfile
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from bridge_agent.bootstrap.config import PluginCatalog, read_yaml
from bridge_agent.contracts.errors import ConfigurationError
from bridge_agent.contracts.plugins import PluginContext, PluginDefinition
from bridge_agent.kernel.dynamic import Context, DynamicHost


class Entry(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    id: str = Field(min_length=1, pattern=r"^[a-zA-Z0-9_.-]+$")
    name: str | None = None
    parent: str | None = None
    config: dict[str, object] = Field(default_factory=dict)
    disabled: bool = False
    scope: dict[str, str] = Field(default_factory=dict)
    inject: list[str] = Field(default_factory=list)
    intercept: dict[str, dict[str, object]] = Field(default_factory=dict)


class Tree(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    version: int = Field(ge=2, le=2)
    entries: list[Entry]


class Group:
    async def activate(self, context: PluginContext) -> None:
        pass


GROUP = PluginDefinition("group", lambda config: Group)


class DynamicLoader:
    def __init__(self, host: DynamicHost, catalog: PluginCatalog) -> None:
        self.host = host
        self.catalog = catalog
        self._entries: dict[str, Entry] = {}
        self._mounted: list[str] = []
        self._contexts: dict[str, Context] = {}

    def resolve(self, instance_id: str) -> Entry:
        return self._entries[instance_id].model_copy(deep=True)

    def context(self, instance_id: str) -> Context:
        return self._contexts[instance_id]

    def locate(self, instance_id: str) -> str:
        entry = self._entries[instance_id]
        return (
            f"{self.locate(entry.parent)}/{instance_id}"
            if entry.parent
            else instance_id
        )

    async def load(self, path: Path) -> None:
        tree = Tree.model_validate(read_yaml(path))
        await self._apply(tree.entries)

    async def create(
        self, options: Mapping[str, object], *, position: int | None = None
    ) -> None:
        entry = Entry.model_validate(dict(options))
        entries = list(self._entries.values())
        entries.insert(len(entries) if position is None else position, entry)
        await self._apply(entries)

    async def update(self, instance_id: str, **changes: object) -> None:
        previous = self._entries[instance_id]
        candidate = Entry.model_validate({**previous.model_dump(), **changes})
        if candidate.id != instance_id:
            raise ConfigurationError("Cannot change an entry ID")
        await self._apply(
            [
                candidate if entry.id == instance_id else entry
                for entry in self._entries.values()
            ]
        )

    async def remove(self, instance_id: str) -> None:
        prefix = self.locate(instance_id)
        await self._apply(
            [
                entry
                for entry in self._entries.values()
                if self.locate(entry.id) != prefix
                and not self.locate(entry.id).startswith(prefix + "/")
            ]
        )

    def save(self, path: Path) -> None:
        data = {
            "version": 2,
            "entries": [
                entry.model_dump(exclude_defaults=True)
                for entry in self._entries.values()
            ],
        }
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, delete=False
            ) as stream:
                temporary = Path(stream.name)
                yaml.safe_dump(data, stream, sort_keys=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    async def _apply(self, entries: list[Entry]) -> None:
        async with self.host.transaction():
            previous = list(self._entries.values())
            try:
                await self._apply_entries(entries)
            except BaseException as error:
                try:
                    await self._apply_entries(previous)
                except BaseException as rollback:
                    raise BaseExceptionGroup(
                        "Tree update and rollback failed", [error, rollback]
                    ) from None
                raise

    async def _apply_entries(self, entries: list[Entry]) -> None:
        pending = {entry.id: entry for entry in entries}
        if len(pending) != len(entries):
            raise ConfigurationError("Duplicate dynamic entry ID")
        ordered: dict[str, Entry] = {}
        while pending:
            ready = [
                entry
                for entry in pending.values()
                if entry.parent is None or entry.parent in ordered
            ]
            if not ready:
                raise ConfigurationError("Missing or cyclic entry parent")
            for entry in ready:
                ordered[entry.id] = entry
                del pending[entry.id]
        definitions = {
            entry.id: self.catalog.definition(entry.name) if entry.name else GROUP
            for entry in ordered.values()
        }
        keys = {
            key.name: key
            for definition in definitions.values()
            for key in (*definition.requires, *definition.provides)
        }
        keys.update({key.name: key for key in self.catalog.service_keys})
        contexts: dict[str, Context] = {}
        for entry in ordered.values():
            context = contexts[entry.parent] if entry.parent else self.host.context
            for name, label in entry.scope.items():
                if name not in keys:
                    raise ConfigurationError("Unknown service in scope")
                context = context.isolate(keys[name], label)
            for name, config in entry.intercept.items():
                if name not in keys:
                    raise ConfigurationError("Unknown intercept service")
                context = context.intercept(keys[name], config)
            if any(name not in keys for name in entry.inject):
                raise ConfigurationError("Unknown injected service")
            definition = definitions[entry.id]
            definitions[entry.id] = replace(
                definition,
                requires=tuple(
                    dict.fromkeys(
                        (*definition.requires, *(keys[name] for name in entry.inject))
                    )
                ),
            )
            contexts[entry.id] = context
            checked = (
                definition.validate_config(entry.config)
                if definition.validate_config
                else entry.config
            )
            definitions[entry.id].prepare(checked)
        enabled: set[str] = set()
        remount: set[str] = set()
        for entry in ordered.values():
            previous = self._entries.get(entry.id)
            if previous is not None and (
                previous.model_dump(exclude={"config"})
                != entry.model_dump(exclude={"config"})
                or entry.parent in remount
            ):
                remount.add(entry.id)
            if not entry.disabled and (entry.parent is None or entry.parent in enabled):
                enabled.add(entry.id)
        previous_entries = self._entries
        self._entries = ordered
        self._contexts = contexts
        for ident in reversed(tuple(self._mounted)):
            if ident not in enabled or ident in remount:
                await self.host.unmount(ident)
                self._mounted.remove(ident)
        for entry in ordered.values():
            if entry.id not in enabled:
                continue
            if entry.id not in self._mounted:
                self._mounted.append(entry.id)
                await self.host.mount(
                    entry.id,
                    definitions[entry.id],
                    entry.config,
                    context=contexts[entry.id],
                )
            elif previous_entries[entry.id].config != entry.config:
                if not await self.host.reconfigure(entry.id, entry.config):
                    raise ConfigurationError("Entry update was vetoed")
        self._entries = ordered
        self._contexts = contexts
