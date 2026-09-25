"""Errors with safe, contextual messages at configuration and host boundaries."""


class BridgeAgentError(Exception):
    """Base for expected configuration and plugin-host errors."""


class ConfigurationError(BridgeAgentError):
    """Configuration could not be parsed or validated."""


class AgentInputError(BridgeAgentError):
    """A request does not belong to a valid workspace or session."""


class AgentLimitError(BridgeAgentError):
    """The runtime stopped at its configured execution budget."""


class AgentSessionError(BridgeAgentError):
    """An interrupted session cannot be continued in this stage."""


class AgentBusyError(BridgeAgentError):
    """The single Agent is already executing a request."""


class AgentTimeoutError(BridgeAgentError):
    """A run exceeded its deadline."""


class AgentExecutionError(BridgeAgentError):
    """A model or tool failed; the public message excludes provider payloads."""


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


class WorkspaceAccessError(BridgeAgentError):
    """A workspace operation was rejected or could not be completed."""


class SkillError(BridgeAgentError):
    """A skill could not be discovered, validated, or loaded."""


class ActionError(BridgeAgentError):
    """A workspace change or command was denied or failed."""
