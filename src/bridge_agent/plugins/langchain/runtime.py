"""Adapt LangChain's execution loop to the public Agent runtime contract."""

import asyncio
import hashlib
import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import NotRequired, cast
from uuid import uuid4

from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain.agents.middleware.types import InputAgentState
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool
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
from bridge_agent.contracts.sessions import SessionInfo
from bridge_agent.plugins.langchain.services import Checkpoint

logger = logging.getLogger("bridge_agent.runtime")


class WorkspaceAgentState(AgentState):
    workspace: NotRequired[str]
    completion: NotRequired[str]
    runtime_version: NotRequired[str]


class LangChainRuntime:
    def __init__(
        self,
        model: BaseChatModel,
        tools: Sequence[BaseTool],
        checkpoint: Checkpoint,
        *,
        max_model_calls: int = 8,
        timeout_seconds: float = 60,
        system_prompt: str = "You are a helpful assistant. Use available tools for arithmetic.",
        workspace: Path | None = None,
    ) -> None:
        self._version = hashlib.sha256(
            json.dumps(
                {
                    "schema": 1,
                    "model": f"{type(model).__module__}.{type(model).__qualname__}",
                    "model_name": getattr(model, "model_name", None),
                    "tools": [(item.name, item.args) for item in tools],
                    "system_prompt": system_prompt,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        self._agent = create_agent(
            model=model,
            tools=tools,
            checkpointer=checkpoint,
            state_schema=WorkspaceAgentState,
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

    async def get_session(self, session_id: str) -> SessionInfo | None:
        snapshot = await self._agent.aget_state(
            {"configurable": {"thread_id": session_id}}
        )
        if not snapshot.values:
            return None
        workspace = Path(snapshot.values["workspace"])
        if self._workspace is not None and workspace != self._workspace:
            raise AgentSessionError("Saved session belongs to another workspace")
        return SessionInfo(
            session_id,
            workspace,
            "succeeded"
            if snapshot.values.get("completion") == "succeeded"
            else "incomplete",
            snapshot.values.get("runtime_version") == self._version,
        )

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
                previous = await self._agent.aget_state(
                    {"configurable": {"thread_id": request.session_id}}
                )
                if previous.values.get("workspace", str(workspace)) != str(workspace):
                    raise AgentSessionError(
                        "Saved session belongs to another workspace"
                    )
                if previous.values and previous.values.get("completion") != "succeeded":
                    raise AgentSessionError(
                        "Saved session is incomplete; start a new session"
                    )
                if (
                    previous.values
                    and previous.values.get("runtime_version") != self._version
                ):
                    raise AgentSessionError(
                        "Saved session is not compatible with this runtime configuration"
                    )
                state: WorkspaceAgentState = {
                    "messages": [user],
                    "workspace": str(workspace),
                    "completion": "incomplete",
                    "runtime_version": self._version,
                }
                result = await self._agent.ainvoke(
                    # create_agent's return annotation retains the base input
                    # type even when a custom state_schema is supplied.
                    cast(InputAgentState, state),
                    {
                        "configurable": {"thread_id": request.session_id},
                        "recursion_limit": self._recursion_limit,
                    },
                    # Commit the incomplete marker before any model/tool step.
                    durability="sync",
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
                await self._agent.aupdate_state(
                    {"configurable": {"thread_id": request.session_id}},
                    {"completion": "succeeded"},
                    as_node="model",
                )
        except AgentSessionError:
            raise
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
