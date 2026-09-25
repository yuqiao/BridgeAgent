"""OpenAI-compatible chat model with explicitly owned HTTP resources."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

import httpx
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from bridge_agent.contracts.errors import ConfigurationError
from bridge_agent.contracts.plugins import Plugin, PluginContext, PluginDefinition
from bridge_agent.plugins.langchain.services import MODEL


class OpenAIConfig(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)

    base_url_env: str = "OPENAI_BASE_URL"
    api_key_env: str = "OPENAI_API_KEY"
    model_env: str = "OPENAI_MODEL"
    timeout_seconds: float = Field(default=30, gt=0, le=600)
    max_retries: int = Field(default=1, ge=0, le=3)
    max_tokens: int = Field(default=4096, ge=1, le=32768)


@dataclass
class OpenAIPlugin:
    config: OpenAIConfig
    base_url: str = field(repr=False)
    model_name: str = field(repr=False)
    api_key: SecretStr = field(repr=False)

    async def activate(self, context: PluginContext) -> None:
        sync_client = context.enter_context(httpx.Client())
        async_client = await context.enter_async_context(httpx.AsyncClient())
        model = ChatOpenAI(
            model=self.model_name,
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=self.config.timeout_seconds,
            max_retries=self.config.max_retries,
            max_completion_tokens=self.config.max_tokens,
            use_responses_api=False,
            http_client=sync_client,
            http_async_client=async_client,
            stream_usage=False,
        )
        context.provide(MODEL, model)


def openai_definition(environment: Mapping[str, str]) -> PluginDefinition:
    def prepare(config: Mapping[str, object]) -> Callable[[], Plugin]:
        parsed = OpenAIConfig.model_validate(dict(config))
        values = [
            environment.get(key, "")
            for key in (parsed.base_url_env, parsed.model_env, parsed.api_key_env)
        ]
        if not all(value.strip() for value in values):
            raise ConfigurationError("Required model environment variables are missing")
        base_url, model, key = values
        secret = SecretStr(key)
        return lambda: OpenAIPlugin(parsed, base_url, model, secret)

    return PluginDefinition("model.openai", prepare, provides=(MODEL,))
