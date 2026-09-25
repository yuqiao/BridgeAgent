"""Versioned Python entry point export; each entry point names one plugin."""

from dataclasses import dataclass

from bridge_agent.contracts.plugins import PluginDefinition


@dataclass(frozen=True)
class PluginExport:
    api_version: int
    definition: PluginDefinition
