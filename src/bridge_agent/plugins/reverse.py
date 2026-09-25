"""Plugin authoring tutorial: another provider for the existing text capability."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from bridge_agent.contracts.demo import TEXT_TRANSFORM
from bridge_agent.contracts.plugins import Plugin, PluginContext, PluginDefinition


class ReverseConfig(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)

    suffix: str = ""


@dataclass
class ReverseTransform:
    suffix: str

    def transform(self, text: str) -> str:
        return text[::-1] + self.suffix


@dataclass
class ReversePlugin:
    suffix: str

    async def activate(self, context: PluginContext) -> None:
        context.provide(TEXT_TRANSFORM, ReverseTransform(self.suffix))


def prepare_reverse(config: Mapping[str, object]) -> Callable[[], Plugin]:
    parsed = ReverseConfig.model_validate(dict(config))
    return lambda: ReversePlugin(suffix=parsed.suffix)


REVERSE = PluginDefinition(
    name="demo.reverse",
    prepare=prepare_reverse,
    provides=(TEXT_TRANSFORM,),
)
