"""Explicit composition catalog for the first Agent runtime."""

import os
from collections.abc import Iterable, Mapping
from pathlib import Path

from dotenv import dotenv_values

from bridge_agent.bootstrap.config import PluginCatalog
from bridge_agent.contracts.errors import ConfigurationError
from bridge_agent.contracts.plugins import PluginDefinition
from bridge_agent.plugins.langchain.builtin import ARITHMETIC, MEMORY, RUNTIME
from bridge_agent.plugins.langchain.openai_model import openai_definition


def agent_catalog(
    extra: Iterable[PluginDefinition] = (),
    *,
    environment: Mapping[str, str] | None = None,
) -> PluginCatalog:
    values = dict(os.environ) if environment is None else environment
    return PluginCatalog(
        (ARITHMETIC, MEMORY, RUNTIME, openai_definition(values), *extra)
    )


def read_environment(path: Path | None, *, override: bool = False) -> dict[str, str]:
    values: dict[str, str] = {}
    if path is not None:
        try:
            with path.open(encoding="utf-8") as stream:
                values = {
                    key: value
                    for key, value in dotenv_values(
                        stream=stream, interpolate=False
                    ).items()
                    if value is not None
                }
        except (OSError, UnicodeError):
            raise ConfigurationError(
                "Cannot read the specified UTF-8 environment file"
            ) from None
    return {**os.environ, **values} if override else {**values, **os.environ}
