"""SQLite checkpoint provider for completed conversation recovery."""

import fcntl
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel, ConfigDict, Field

from bridge_agent.contracts.errors import ConfigurationError
from bridge_agent.contracts.plugins import Plugin, PluginContext, PluginDefinition
from bridge_agent.plugins.langchain.services import CHECKPOINT


class SQLiteConfig(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    path: str = Field(min_length=1)


@dataclass
class SQLitePlugin:
    config: SQLiteConfig

    async def activate(self, context: PluginContext) -> None:
        path = Path(self.config.path).expanduser().resolve()
        if path.is_dir():
            raise ConfigurationError("Session database path must be a file")
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = context.enter_context(path.with_name(path.name + ".lock").open("a"))
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ConfigurationError("Session database is already in use") from None
        checkpoint = await context.enter_async_context(
            AsyncSqliteSaver.from_conn_string(str(path))
        )
        await checkpoint.setup()
        context.provide(CHECKPOINT, checkpoint)


def prepare(config: Mapping[str, object]) -> Callable[[], Plugin]:
    parsed = SQLiteConfig.model_validate(dict(config))
    return lambda: SQLitePlugin(parsed)


SQLITE = PluginDefinition("checkpoint.sqlite", prepare, provides=(CHECKPOINT,))
