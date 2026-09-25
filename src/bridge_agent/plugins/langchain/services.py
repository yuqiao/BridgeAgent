"""Adapter-local capability keys; never imported by the application or kernel."""

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver

from bridge_agent.contracts.plugins import ServiceKey

MODEL = ServiceKey[BaseChatModel]("langchain.model")
TOOLS = ServiceKey[tuple[BaseTool, ...]]("langchain.tools")
type Checkpoint = BaseCheckpointSaver[int] | BaseCheckpointSaver[str]
CHECKPOINT = ServiceKey[Checkpoint]("langchain.checkpoint")
