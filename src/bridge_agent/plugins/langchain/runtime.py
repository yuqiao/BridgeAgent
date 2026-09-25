"""Adapt LangChain's execution loop to the public Agent runtime contract."""

import asyncio
import logging
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import GraphRecursionError

from bridge_agent.contracts.agent import RunRequest, RunResult, ToolResult
from bridge_agent.contracts.errors import (
    AgentBusyError,
    AgentExecutionError,
    AgentInputError,
    AgentLimitError,
    AgentSessionError,
    AgentTimeoutError,
)

logger = logging.getLogger("bridge_agent.runtime")


class LangChainRuntime:
    def __init__(
        self,
        model: BaseChatModel,
        tools: Sequence[BaseTool],
        checkpoint: BaseCheckpointSaver[int],
        *,
        max_model_calls: int = 8,
        timeout_seconds: float = 60,
        system_prompt: str = "You are a helpful assistant. Use available tools for arithmetic.",
        workspace: Path | None = None,
    ) -> None:
        self._agent = create_agent(
            model=model,
            tools=tools,
            checkpointer=checkpoint,
            system_prompt=system_prompt,
            middleware=[
                ModelCallLimitMiddleware(
                    run_limit=max_model_calls, exit_behavior="error"
                )
            ],
        )
        self._recursion_limit = max_model_calls * 6 + 10
        self._failed_sessions: set[str] = set()
        self._busy = False
        self._timeout_seconds = timeout_seconds
        self._workspace = workspace.resolve() if workspace is not None else None

    async def run(self, request: RunRequest) -> RunResult:
        if self._busy:
            raise AgentBusyError("Agent already has a running request")
        workspace = request.workspace.resolve()
        if not request.text.strip() or not request.session_id.strip():
            raise AgentInputError("Text and session ID must be non-empty")
        if not workspace.is_dir():
            raise AgentInputError("Workspace must be an existing directory")
        if self._workspace is not None and workspace != self._workspace:
            raise AgentInputError("A runtime is bound to one workspace")
        self._workspace = workspace
        if request.session_id in self._failed_sessions:
            raise AgentSessionError("Previous run failed; start a new session")
        user = HumanMessage(content=request.text, id=str(uuid4()))
        self._busy = True
        logger.info("run_started", extra={"run_id": user.id})
        try:
            async with asyncio.timeout(self._timeout_seconds):
                result = await self._agent.ainvoke(
                    {"messages": [user]},
                    {
                        "configurable": {"thread_id": request.session_id},
                        "recursion_limit": self._recursion_limit,
                    },
                )
                messages = result["messages"]
                start = next(
                    i for i, message in enumerate(messages) if message.id == user.id
                )
                current = messages[start + 1 :]
                final = current[-1]
                if (
                    not isinstance(final, AIMessage)
                    or final.tool_calls
                    or not final.text.strip()
                ):
                    raise ValueError("Agent did not produce a final text answer")
        except (ModelCallLimitExceededError, GraphRecursionError) as error:
            logger.info("run_failed", extra={"run_id": user.id, "reason": "limit"})
            self._failed_sessions.add(request.session_id)
            raise AgentLimitError(
                "Agent execution limit reached; start a new session"
            ) from error
        except asyncio.CancelledError:
            logger.info("run_cancelled", extra={"run_id": user.id})
            self._failed_sessions.add(request.session_id)
            raise
        except TimeoutError as error:
            logger.info("run_failed", extra={"run_id": user.id, "reason": "timeout"})
            self._failed_sessions.add(request.session_id)
            raise AgentTimeoutError(
                "Agent request timed out; start a new session"
            ) from error
        except Exception as error:
            logger.info("run_failed", extra={"run_id": user.id, "reason": "execution"})
            self._failed_sessions.add(request.session_id)
            raise AgentExecutionError(
                "Model or tool execution failed; start a new session"
            ) from error
        finally:
            self._busy = False
        tools = tuple(
            ToolResult(
                message.name or "", message.tool_call_id, message.text, message.status
            )
            for message in current
            if isinstance(message, ToolMessage)
        )
        for item in tools:
            logger.info(
                "tool_completed",
                extra={"run_id": user.id, "tool": item.name, "status": item.status},
            )
        logger.info("run_completed", extra={"run_id": user.id})
        return RunResult(final.text, tools)
