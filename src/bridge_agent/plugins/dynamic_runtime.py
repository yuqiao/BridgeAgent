"""Agent adapter resolves the current runtime and applies Context extensions."""

from collections.abc import Callable

from bridge_agent.contracts.agent import AGENT_RUNTIME, RunRequest, RunResult
from bridge_agent.contracts.errors import PluginProtocolError
from bridge_agent.contracts.sessions import SESSION_CONTROL, SessionInfo
from bridge_agent.kernel.dynamic import Context, DynamicHost


class DynamicAgentRuntime:
    def __init__(
        self, host: DynamicHost, context: Context | Callable[[], Context] | None = None
    ) -> None:
        self._host = host
        self._context = (
            context if callable(context) else lambda: context or host.context
        )

    async def run(self, request: RunRequest) -> RunResult:
        context = self._context()
        async with self._host.lease(context):

            async def execute() -> RunResult:
                return await context.require(AGENT_RUNTIME).run(request)

            result = await context.waterfall("agent.run", request, next=execute)
            if not isinstance(result, RunResult):
                raise PluginProtocolError("Agent extension must return RunResult")
            return result

    async def get_session(self, session_id: str) -> SessionInfo | None:
        async with self._host.lease(self._context()) as context:
            return await context.require(SESSION_CONTROL).get_session(session_id)
