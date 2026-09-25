"""Agent adapter resolves the current runtime and applies Context extensions."""

from bridge_agent.contracts.agent import AGENT_RUNTIME, RunRequest, RunResult
from bridge_agent.contracts.errors import PluginProtocolError
from bridge_agent.kernel.dynamic import Context, DynamicHost


class DynamicAgentRuntime:
    def __init__(self, host: DynamicHost, context: Context | None = None) -> None:
        self._context = context or host.context

    async def run(self, request: RunRequest) -> RunResult:
        async def execute() -> RunResult:
            return await self._context.require(AGENT_RUNTIME).run(request)

        result = await self._context.waterfall("agent.run", request, next=execute)
        if not isinstance(result, RunResult):
            raise PluginProtocolError("Agent extension must return RunResult")
        return result
