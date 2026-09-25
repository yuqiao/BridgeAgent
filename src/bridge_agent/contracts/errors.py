"""Errors with safe, contextual messages at configuration and host boundaries."""


class BridgeAgentError(Exception):
    """Base for expected configuration and plugin-host errors."""


class ConfigurationError(BridgeAgentError):
    """Configuration could not be parsed or validated."""


class DependencyError(BridgeAgentError):
    """Plugin declarations cannot form a valid activation plan."""


class PluginProtocolError(BridgeAgentError):
    """A plugin violated its declared capability or context lifetime."""


class HostStateError(BridgeAgentError):
    """The requested operation is not valid in the current host state."""


class PluginActivationError(BridgeAgentError):
    """A named plugin failed construction or activation."""


class PluginCleanupError(BridgeAgentError):
    """A cleanup failed; its original exception is preserved as the cause."""
