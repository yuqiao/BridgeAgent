"""Exercise the real Agent loop, replacing only the external model boundary."""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from bridge_agent.contracts.agent import RunRequest
from bridge_agent.plugins.langchain.runtime import LangChainRuntime


class CalculatorModel(BaseChatModel):
    @property
    def _llm_type(self):
        return "test-calculator"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if isinstance(messages[-1], ToolMessage):
            message = AIMessage(content=f"answer: {messages[-1].content}")
        else:
            message = AIMessage(
                content="",
                tool_calls=[{"name": "add", "args": {"a": 19, "b": 23}, "id": "sum-1"}],
            )
        return ChatResult(generations=[ChatGeneration(message=message)])


@tool
def add(a: int, b: int) -> str:
    """Add two integers."""
    return str(a + b)


def test_runtime_returns_answer_grounded_in_executed_tool_result(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        runtime = LangChainRuntime(CalculatorModel(), [add], InMemorySaver())
        result = await runtime.run(RunRequest("session-a", "Add 19 and 23", tmp_path))
        assert result.text == "answer: 42"
        assert [(item.name, item.content) for item in result.tools] == [("add", "42")]

    asyncio.run(scenario())


class MemoryModel(CalculatorModel):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        text = "|".join(
            message.text for message in messages if isinstance(message, HumanMessage)
        )
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])


def test_turns_share_checkpoint_history_but_other_sessions_do_not(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        runtime = LangChainRuntime(MemoryModel(), [], InMemorySaver())
        assert (await runtime.run(RunRequest("one", "alpha", tmp_path))).text == "alpha"
        assert (
            await runtime.run(RunRequest("one", "beta", tmp_path))
        ).text == "alpha|beta"
        assert (await runtime.run(RunRequest("two", "gamma", tmp_path))).text == "gamma"

    asyncio.run(scenario())


def test_runtime_rejects_switching_workspace_without_sharing_history(
    tmp_path: Path,
) -> None:
    from bridge_agent.contracts.errors import AgentInputError

    async def scenario() -> None:
        other = tmp_path / "other"
        other.mkdir()
        runtime = LangChainRuntime(MemoryModel(), [], InMemorySaver())
        await runtime.run(RunRequest("one", "private", tmp_path))
        with pytest.raises(AgentInputError, match="workspace"):
            await runtime.run(RunRequest("one", "hello", other))

    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["empty-text", "empty-session", "missing-workspace"])
def test_invalid_requests_fail_before_model_execution(
    tmp_path: Path, case: str
) -> None:
    from bridge_agent.contracts.errors import AgentInputError

    async def scenario() -> None:
        runtime = LangChainRuntime(MemoryModel(), [], InMemorySaver())
        request = RunRequest(
            " " if case == "empty-session" else "one",
            " " if case == "empty-text" else "hello",
            tmp_path / "missing" if case == "missing-workspace" else tmp_path,
        )
        with pytest.raises(AgentInputError):
            await runtime.run(request)

    asyncio.run(scenario())


def test_model_call_limit_stops_a_loop_and_requires_a_new_session(
    tmp_path: Path,
) -> None:
    from bridge_agent.contracts.errors import AgentLimitError, AgentSessionError

    class LoopModel(CalculatorModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            return super()._generate([HumanMessage(content="again")])

    async def scenario() -> None:
        runtime = LangChainRuntime(
            LoopModel(), [add], InMemorySaver(), max_model_calls=2
        )
        with pytest.raises(AgentLimitError):
            await runtime.run(RunRequest("one", "loop", tmp_path))
        with pytest.raises(AgentSessionError, match="new session"):
            await runtime.run(RunRequest("one", "retry", tmp_path))

    asyncio.run(scenario())


class WaitingModel(CalculatorModel):
    entered: Any
    stopped: Any

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        self.entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.stopped.set()


def test_cancelled_run_stops_model_and_rejects_continuation(tmp_path: Path) -> None:
    from bridge_agent.contracts.errors import AgentSessionError

    async def scenario() -> None:
        entered, stopped = asyncio.Event(), asyncio.Event()
        runtime = LangChainRuntime(
            WaitingModel(entered=entered, stopped=stopped), [], InMemorySaver()
        )
        task = asyncio.create_task(runtime.run(RunRequest("one", "wait", tmp_path)))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stopped.is_set()
        with pytest.raises(AgentSessionError):
            await runtime.run(RunRequest("one", "retry", tmp_path))

    asyncio.run(asyncio.wait_for(scenario(), 2))


def test_a_second_run_is_rejected_while_the_agent_is_busy(tmp_path: Path) -> None:
    from bridge_agent.contracts.errors import AgentBusyError

    async def scenario() -> None:
        entered, stopped = asyncio.Event(), asyncio.Event()
        runtime = LangChainRuntime(
            WaitingModel(entered=entered, stopped=stopped), [], InMemorySaver()
        )
        first = asyncio.create_task(runtime.run(RunRequest("one", "wait", tmp_path)))
        await entered.wait()
        try:
            with pytest.raises(AgentBusyError):
                await runtime.run(RunRequest("two", "hello", tmp_path))
        finally:
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first

    asyncio.run(asyncio.wait_for(scenario(), 2))


def test_run_timeout_cancels_model_and_blocks_incomplete_session(
    tmp_path: Path,
) -> None:
    from bridge_agent.contracts.errors import AgentSessionError, AgentTimeoutError

    async def scenario() -> None:
        stopped = asyncio.Event()
        runtime = LangChainRuntime(
            WaitingModel(entered=asyncio.Event(), stopped=stopped),
            [],
            InMemorySaver(),
            timeout_seconds=0.05,
        )
        with pytest.raises(AgentTimeoutError):
            await runtime.run(RunRequest("one", "wait", tmp_path))
        assert stopped.is_set()
        with pytest.raises(AgentSessionError):
            await runtime.run(RunRequest("one", "retry", tmp_path))

    asyncio.run(asyncio.wait_for(scenario(), 2))


def test_model_failure_has_safe_error_and_blocks_incomplete_session(
    tmp_path: Path,
) -> None:
    from bridge_agent.contracts.errors import AgentExecutionError, AgentSessionError

    class BrokenModel(CalculatorModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            raise ValueError("PRIVATE_KEY_FROM_PROVIDER")

    async def scenario() -> None:
        runtime = LangChainRuntime(BrokenModel(), [], InMemorySaver())
        with pytest.raises(AgentExecutionError) as failure:
            await runtime.run(RunRequest("one", "hello", tmp_path))
        assert "PRIVATE_KEY" not in str(failure.value)
        assert isinstance(failure.value.__cause__, ValueError)
        with pytest.raises(AgentSessionError):
            await runtime.run(RunRequest("one", "retry", tmp_path))

    asyncio.run(scenario())


def test_runtime_logs_execution_metadata_without_message_content(
    tmp_path: Path, caplog
) -> None:
    async def scenario() -> None:
        runtime = LangChainRuntime(CalculatorModel(), [add], InMemorySaver())
        await runtime.run(RunRequest("one", "PRIVATE_USER_MESSAGE", tmp_path))

    with caplog.at_level("INFO", logger="bridge_agent.runtime"):
        asyncio.run(scenario())
    assert [record.message for record in caplog.records] == [
        "run_started",
        "tool_completed",
        "run_completed",
    ]
    assert "PRIVATE_USER_MESSAGE" not in caplog.text


def test_runtime_logs_failed_run_without_provider_exception_body(
    tmp_path: Path, caplog
) -> None:
    from bridge_agent.contracts.errors import AgentExecutionError

    class BrokenModel(CalculatorModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            raise ValueError("PRIVATE_PROVIDER_ERROR")

    async def scenario() -> None:
        runtime = LangChainRuntime(BrokenModel(), [], InMemorySaver())
        with pytest.raises(AgentExecutionError):
            await runtime.run(RunRequest("one", "hello", tmp_path))

    with caplog.at_level("INFO", logger="bridge_agent.runtime"):
        asyncio.run(scenario())
    assert [record.message for record in caplog.records] == [
        "run_started",
        "run_failed",
    ]
    assert "PRIVATE_PROVIDER_ERROR" not in caplog.text


def test_empty_model_answer_is_failure_and_cannot_be_continued(tmp_path: Path) -> None:
    from bridge_agent.contracts.errors import AgentExecutionError, AgentSessionError

    class EmptyModel(CalculatorModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content=""))]
            )

    async def scenario() -> None:
        runtime = LangChainRuntime(EmptyModel(), [], InMemorySaver())
        with pytest.raises(AgentExecutionError):
            await runtime.run(RunRequest("one", "hello", tmp_path))
        with pytest.raises(AgentSessionError):
            await runtime.run(RunRequest("one", "retry", tmp_path))

    asyncio.run(scenario())


def test_tool_protocol_errors_are_visible_in_this_turns_result(tmp_path: Path) -> None:
    class UnknownToolModel(CalculatorModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if isinstance(messages[-1], ToolMessage):
                return super()._generate(messages)
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            tool_calls=[
                                {"name": "missing", "args": {}, "id": "bad-call"}
                            ],
                        )
                    )
                ]
            )

    async def scenario() -> None:
        runtime = LangChainRuntime(UnknownToolModel(), [add], InMemorySaver())
        result = await runtime.run(RunRequest("one", "call a tool", tmp_path))
        assert result.tools[0].status == "error"
        assert "missing" in result.tools[0].content

    asyncio.run(scenario())


def test_each_turn_returns_only_its_own_tool_results(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = LangChainRuntime(CalculatorModel(), [add], InMemorySaver())
        for text in ("first sum", "second sum"):
            result = await runtime.run(RunRequest("one", text, tmp_path))
            assert [(item.name, item.content) for item in result.tools] == [
                ("add", "42")
            ]

    asyncio.run(scenario())
