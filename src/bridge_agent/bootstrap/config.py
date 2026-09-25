"""Strict data-only YAML parsing and plugin-specific validation."""

from collections.abc import Hashable, Iterable
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from yaml.events import AliasEvent, NodeEvent
from yaml.nodes import MappingNode, Node, ScalarNode

from bridge_agent.contracts.errors import ConfigurationError
from bridge_agent.contracts.plugins import PluginDefinition, PreparedPlugin


class _DataError(yaml.MarkedYAMLError):
    """A controlled diagnostic that contains no YAML input values."""


class _DataLoader(yaml.SafeLoader):
    """An isolated loader; never change PyYAML's process-global constructors."""

    def construct_object(self, node: Node, deep: bool = False) -> object:
        allowed = {"str", "bool", "int", "float", "null", "seq", "map"}
        if node.tag not in {f"tag:yaml.org,2002:{tag}" for tag in allowed}:
            raise _DataError(
                problem="unsupported YAML tag", problem_mark=node.start_mark
            )
        try:
            return super().construct_object(node, deep=deep)
        except (ValueError, IndexError, KeyError):
            raise _DataError(
                problem="invalid scalar value", problem_mark=node.start_mark
            ) from None

    def construct_mapping(
        self, node: MappingNode, deep: bool = False
    ) -> dict[Hashable, object]:
        result: dict[Hashable, object] = {}
        for key_node, value_node in node.value:
            if (
                not isinstance(key_node, ScalarNode)
                or key_node.tag != "tag:yaml.org,2002:str"
            ):
                raise _DataError(
                    problem="mapping keys must be strings",
                    problem_mark=key_node.start_mark,
                )
            key = key_node.value
            if key in result:
                raise _DataError(
                    problem="duplicate mapping key", problem_mark=key_node.start_mark
                )
            result[key] = self.construct_object(value_node, deep=deep)
        return result


class _PluginRow(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)

    name: str = Field(min_length=1)
    config: dict[str, object] = Field(default_factory=dict)


class _Document(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)

    version: int = Field(ge=1, le=1)
    plugins: list[_PluginRow]


def _validation_message(error: ValidationError) -> str:
    # Do not include inputs, arbitrary custom validator messages, or contexts.
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or '<root>'}: {item['type']}"
        for item in error.errors(
            include_input=False, include_context=False, include_url=False
        )
    )


class PluginCatalog:
    """Explicit allowlist of definitions; names never become import paths."""

    def __init__(self, definitions: Iterable[PluginDefinition]) -> None:
        self._definitions: dict[str, PluginDefinition] = {}
        for definition in definitions:
            if not definition.name.strip() or definition.name in self._definitions:
                raise ConfigurationError(
                    f"Invalid or duplicate plugin definition: {definition.name!r}"
                )
            self._definitions[definition.name] = definition

    def load(self, path: Path) -> tuple[PreparedPlugin, ...]:
        """Validate every row without constructing or activating plugin instances."""
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise ConfigurationError(
                f"{path}: cannot read UTF-8 configuration"
            ) from None
        try:
            for event in yaml.parse(source, Loader=_DataLoader):
                if isinstance(event, AliasEvent) or (
                    isinstance(event, NodeEvent) and event.anchor is not None
                ):
                    mark = event.start_mark
                    location = f":{mark.line + 1}:{mark.column + 1}" if mark else ""
                    raise ConfigurationError(
                        f"{path}{location}: YAML anchors and aliases are not supported"
                    )
            raw: object = yaml.load(source, Loader=_DataLoader)
        except yaml.YAMLError as error:
            location = ""
            if (
                isinstance(error, yaml.MarkedYAMLError)
                and error.problem_mark is not None
            ):
                mark = error.problem_mark
                location = f":{mark.line + 1}:{mark.column + 1}"
            # Parser exception strings can contain full source lines, including secrets.
            detail = (
                error.problem
                if isinstance(error, _DataError)
                else "invalid or unsupported YAML"
            )
            raise ConfigurationError(f"{path}{location}: {detail}") from None
        try:
            document = _Document.model_validate(raw)
        except ValidationError as error:
            raise ConfigurationError(f"{path}: {_validation_message(error)}") from None

        prepared: list[PreparedPlugin] = []
        seen: set[str] = set()
        for row in document.plugins:
            if row.name in seen:
                raise ConfigurationError(f"{path}: duplicate plugin {row.name}")
            seen.add(row.name)
            definition = self._definitions.get(row.name)
            if definition is None:
                raise ConfigurationError(f"{path}: unknown plugin {row.name}")
            try:
                factory = definition.prepare(row.config)
            except ValidationError as error:
                raise ConfigurationError(
                    f"{path}: plugin {row.name}: {_validation_message(error)}"
                ) from None
            except Exception:
                raise ConfigurationError(
                    f"{path}: plugin {row.name}: configuration preparation failed"
                ) from None
            prepared.append(
                PreparedPlugin(
                    row.name, factory, definition.requires, definition.provides
                )
            )
        return tuple(prepared)
