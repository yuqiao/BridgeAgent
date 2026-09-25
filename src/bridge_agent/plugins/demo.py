"""Two providers and a consumer demonstrating the actual plugin protocol."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from bridge_agent.contracts.demo import REPORT, TEXT_TRANSFORM, TextTransform
from bridge_agent.contracts.plugins import Plugin, PluginContext, PluginDefinition


class _EmptyConfig(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)


class _ReportConfig(_EmptyConfig):
    prefix: str = "result: "


class Uppercase:
    def transform(self, text: str) -> str:
        return text.upper()


class Lowercase:
    def transform(self, text: str) -> str:
        return text.lower()


@dataclass
class _TransformPlugin:
    transform: TextTransform

    async def activate(self, context: PluginContext) -> None:
        context.provide(TEXT_TRANSFORM, self.transform)


@dataclass
class _Report:
    transform: TextTransform
    prefix: str

    def render(self, text: str) -> str:
        return self.prefix + self.transform.transform(text)


@dataclass
class _ReportPlugin:
    prefix: str

    async def activate(self, context: PluginContext) -> None:
        context.provide(REPORT, _Report(context.require(TEXT_TRANSFORM), self.prefix))


def _prepare_uppercase(config: Mapping[str, object]) -> Callable[[], Plugin]:
    _EmptyConfig.model_validate(dict(config))
    return lambda: _TransformPlugin(Uppercase())


def _prepare_lowercase(config: Mapping[str, object]) -> Callable[[], Plugin]:
    _EmptyConfig.model_validate(dict(config))
    return lambda: _TransformPlugin(Lowercase())


def _prepare_report(config: Mapping[str, object]) -> Callable[[], Plugin]:
    parsed = _ReportConfig.model_validate(dict(config))
    return lambda: _ReportPlugin(parsed.prefix)


UPPERCASE = PluginDefinition(
    "demo.uppercase", _prepare_uppercase, provides=(TEXT_TRANSFORM,)
)
LOWERCASE = PluginDefinition(
    "demo.lowercase", _prepare_lowercase, provides=(TEXT_TRANSFORM,)
)
REPORT_PLUGIN = PluginDefinition(
    "demo.report", _prepare_report, requires=(TEXT_TRANSFORM,), provides=(REPORT,)
)
