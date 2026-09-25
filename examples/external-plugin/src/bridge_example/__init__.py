"""An independently distributed runtime; no core source modifications needed."""

from bridge_agent.contracts.agent import AGENT_RUNTIME, RunResult
from bridge_agent.contracts.packages import PluginExport
from bridge_agent.contracts.plugins import PluginDefinition


class ExampleRuntime:
    def __init__(self, prefix):
        self.prefix = prefix

    async def run(self, request):
        return RunResult(text=self.prefix + request.text)


class ExamplePlugin:
    def __init__(self, prefix):
        self.prefix = prefix

    async def activate(self, context):
        context.provide(AGENT_RUNTIME, ExampleRuntime(self.prefix))


def prepare(config):
    if set(config) - {"prefix"} or not isinstance(
        config.get("prefix", "external: "), str
    ):
        raise ValueError("Only a string prefix is supported")
    return lambda: ExamplePlugin(config.get("prefix", "external: "))


plugin = PluginExport(
    api_version=1,
    definition=PluginDefinition(
        "runtime.example",
        prepare,
        provides=(AGENT_RUNTIME,),
    ),
)
