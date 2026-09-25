"""Discover metadata without importing unused third-party packages."""

import importlib.metadata

from bridge_agent.contracts.errors import ConfigurationError
from bridge_agent.contracts.packages import PluginExport
from bridge_agent.contracts.plugins import PluginDefinition


def load_external(name: str, *, builtin: bool = False) -> PluginDefinition | None:
    candidates = [
        entry
        for entry in importlib.metadata.entry_points(group="bridge_agent.plugins")
        if entry.name == name
    ]
    if not candidates:
        return None
    if builtin:
        raise ConfigurationError(f"External plugin name conflict: {name}")
    if len(candidates) != 1:
        raise ConfigurationError(f"Ambiguous external plugin: {name}")
    try:
        export = candidates[0].load()
    except Exception:
        raise ConfigurationError(f"Cannot import external plugin: {name}") from None
    if (
        not isinstance(export, PluginExport)
        or type(export.api_version) is not int
        or export.api_version != 1
    ):
        raise ConfigurationError(f"Incompatible external plugin API: {name}")
    if (
        not isinstance(export.definition, PluginDefinition)
        or export.definition.name != name
    ):
        raise ConfigurationError(f"External plugin name mismatch: {name}")
    return export.definition
