"""Explicit providers of the initial tool set, checkpoint, and Agent runtime."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, ConfigDict, Field

from bridge_agent.contracts.agent import AGENT_RUNTIME
from bridge_agent.contracts.plugins import Plugin, PluginContext, PluginDefinition
from bridge_agent.plugins.langchain.runtime import LangChainRuntime
from bridge_agent.plugins.langchain.services import CHECKPOINT, MODEL, TOOLS


class EmptyConfig(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)


@tool
async def add(a: int, b: int) -> str:
    """Add two integers and return their exact sum."""
    return str(a + b)


class ArithmeticPlugin:
    async def activate(self, context: PluginContext) -> None:
        context.provide(TOOLS, (add,))


class MemoryPlugin:
    async def activate(self, context: PluginContext) -> None:
        checkpoint = await context.enter_async_context(InMemorySaver())
        context.provide(CHECKPOINT, checkpoint)


class RuntimeConfig(EmptyConfig):
    max_model_calls: int = Field(default=8, ge=1, le=100)
    timeout_seconds: float = Field(default=60, gt=0, le=600)
    system_prompt: str = Field(
        default="You are a helpful assistant. Use available tools for arithmetic.",
        min_length=1,
    )


@dataclass
class RuntimePlugin:
    config: RuntimeConfig
    workspace: Path | None = None

    async def activate(self, context: PluginContext) -> None:
        tools = context.require(TOOLS)
        runtime = LangChainRuntime(
            context.require(MODEL),
            tools,
            context.require(CHECKPOINT),
            max_model_calls=self.config.max_model_calls,
            timeout_seconds=self.config.timeout_seconds,
            system_prompt=self.config.system_prompt,
            workspace=self.workspace,
        )
        context.provide(AGENT_RUNTIME, runtime)


def prepare_arithmetic(config: Mapping[str, object]) -> Callable[[], Plugin]:
    EmptyConfig.model_validate(dict(config))
    return ArithmeticPlugin


def prepare_memory(config: Mapping[str, object]) -> Callable[[], Plugin]:
    EmptyConfig.model_validate(dict(config))
    return MemoryPlugin


def prepare_runtime(config: Mapping[str, object]) -> Callable[[], Plugin]:
    parsed = RuntimeConfig.model_validate(dict(config))
    return lambda: RuntimePlugin(parsed)


ARITHMETIC = PluginDefinition("tools.arithmetic", prepare_arithmetic, provides=(TOOLS,))
MEMORY = PluginDefinition("checkpoint.memory", prepare_memory, provides=(CHECKPOINT,))
RUNTIME = PluginDefinition(
    "runtime.langchain",
    prepare_runtime,
    requires=(MODEL, TOOLS, CHECKPOINT),
    provides=(AGENT_RUNTIME,),
)


def runtime_definition(workspace: Path | None) -> PluginDefinition:
    def prepare(config: Mapping[str, object]) -> Callable[[], Plugin]:
        parsed = RuntimeConfig.model_validate(dict(config))
        return lambda: RuntimePlugin(parsed, workspace)

    return PluginDefinition(
        "runtime.langchain",
        prepare,
        requires=(MODEL, TOOLS, CHECKPOINT),
        provides=(AGENT_RUNTIME,),
    )
