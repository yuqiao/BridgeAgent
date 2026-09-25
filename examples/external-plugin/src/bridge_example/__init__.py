"""An independently distributed runtime; no core source modifications needed."""

from bridge_agent.contracts.agent import AGENT_RUNTIME, RunResult
from bridge_agent.contracts.packages import PluginExport
from bridge_agent.contracts.plugins import PluginDefinition


class ExampleRuntime:
    async def run(self, request):
        return RunResult(text="external: " + request.text)


class ExamplePlugin:
    async def activate(self, context):
        context.provide(AGENT_RUNTIME, ExampleRuntime())


def prepare(config):
    if config:
        raise ValueError("No options supported")
    return ExamplePlugin


plugin = PluginExport(
    api_version=1,
    definition=PluginDefinition(
        "runtime.example",
        prepare,
        provides=(AGENT_RUNTIME,),
    ),
)
