"""One serial conversation; checkpoint history belongs to the runtime."""

from pathlib import Path
from uuid import uuid4

from bridge_agent.contracts.agent import AgentRuntime, RunRequest, RunResult


class AgentSession:
    def __init__(
        self, runtime: AgentRuntime, workspace: Path, session_id: str | None = None
    ) -> None:
        self._runtime = runtime
        self.workspace = workspace.resolve()
        self.session_id = session_id or str(uuid4())

    async def ask(self, text: str) -> RunResult:
        return await self._runtime.run(
            RunRequest(self.session_id, text, self.workspace)
        )
