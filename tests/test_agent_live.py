"""Explicit external endpoint acceptance; no credentials or payloads are printed."""

import asyncio
from pathlib import Path

import pytest

from bridge_agent.application.agent import AgentSession
from bridge_agent.bootstrap.agent import agent_catalog, read_environment
from bridge_agent.contracts.agent import AGENT_RUNTIME
from bridge_agent.kernel.host import PluginHost


@pytest.mark.live
def test_live_tool_round_trip_and_followup() -> None:
    root = Path(__file__).resolve().parents[1]

    async def scenario() -> None:
        catalog = agent_catalog(
            environment=read_environment(root / ".env", override=True)
        )
        async with PluginHost(catalog.load(root / "examples/agent.yaml")) as host:
            session = AgentSession(host.resolve(AGENT_RUNTIME), root)
            result = await session.ask(
                "请务必调用 add 工具计算 1847 + 2965，并在最终回答给出工具返回的结果。记住本轮结果。"
            )
            assert any(
                item.name == "add" and item.content == "4812" for item in result.tools
            ), "Expected executed add tool result"
            assert "4812" in result.text, "Expected final answer to use tool result"
            followup = await session.ask(
                "上一轮工具返回的数字是多少？只回答那个数字，不要重新调用工具。"
            )
            assert "4812" in followup.text, "Expected conversation memory"
            assert followup.tools == (), "Expected only this turn's tool records"

    failure = None
    try:
        asyncio.run(scenario())
    except Exception as error:
        chain = []
        current: BaseException | None = error
        while current is not None:
            chain.append(type(current).__name__)
            current = current.__cause__
        failure = "Live acceptance failed: " + " <- ".join(chain)
    if failure is not None:
        pytest.fail(failure, pytrace=False)
