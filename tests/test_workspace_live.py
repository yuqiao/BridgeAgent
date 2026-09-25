"""Opt-in acceptance of real repository tool calls against a tiny fixture."""

import asyncio
from pathlib import Path

import pytest

from bridge_agent.application.agent import AgentSession
from bridge_agent.bootstrap.agent import agent_catalog, read_environment
from bridge_agent.contracts.agent import AGENT_RUNTIME
from bridge_agent.kernel.host import PluginHost


@pytest.mark.live
def test_live_repository_answer_cites_a_read_file(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    (tmp_path / "answer.py").write_text("def answer():\n    return 42\n")

    async def scenario() -> None:
        catalog = agent_catalog(
            workspace=tmp_path,
            environment=read_environment(root / ".env", override=True),
        )
        async with PluginHost(catalog.load(root / "examples/workspace.yaml")) as host:
            result = await AgentSession(host.resolve(AGENT_RUNTIME), tmp_path).ask(
                "请实际读取 answer.py，说明 answer 函数返回什么，引用文件路径和行号。"
            )
            assert any(
                item.name == "read_file" and item.status == "success"
                for item in result.tools
            )
            assert (
                "answer.py" in result.text
                and "42" in result.text
                and "2" in result.text
            )

    failure = None
    try:
        asyncio.run(scenario())
    except Exception as error:
        names = []
        current: BaseException | None = error
        while current is not None:
            names.append(type(current).__name__)
            current = current.__cause__
        failure = "Live workspace acceptance failed: " + " <- ".join(names)
    if failure:
        pytest.fail(failure, pytrace=False)
