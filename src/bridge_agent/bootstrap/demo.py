"""Composition root for the plugin demonstration."""

from bridge_agent.bootstrap.config import PluginCatalog
from bridge_agent.plugins.demo import LOWERCASE, REPORT_PLUGIN, UPPERCASE
from bridge_agent.plugins.reverse import REVERSE


def demo_catalog() -> PluginCatalog:
    return PluginCatalog((UPPERCASE, LOWERCASE, REVERSE, REPORT_PLUGIN))
