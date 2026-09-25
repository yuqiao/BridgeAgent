"""Strict data-only YAML parsing and plugin-specific validation."""

from collections.abc import Hashable, Iterable
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from yaml.events import AliasEvent, NodeEvent
from yaml.nodes import MappingNode, Node, ScalarNode

from bridge_agent.bootstrap.packages import load_external
from bridge_agent.contracts.errors import ConfigurationError
from bridge_agent.contracts.plugins import (
    PluginDefinition,
    PreparedPlugin,
    ServiceIdentity,
)


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
    include: list[str] = Field(default_factory=list)
    plugins: list[_PluginRow] = Field(default_factory=list)


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

    @property
    def service_keys(self) -> tuple[ServiceIdentity, ...]:
        return tuple(
            dict.fromkeys(
                key
                for definition in self._definitions.values()
                for key in (*definition.requires, *definition.provides)
            )
        )

    def definition(self, name: str) -> PluginDefinition:
        external = load_external(name, builtin=name in self._definitions)
        result = self._definitions.get(name) or external
        if result is None:
            raise ConfigurationError(f"unknown plugin {name}")
        return result

    @staticmethod
    def _read(path: Path) -> _Document:
        raw = read_yaml(path)
        try:
            document = _Document.model_validate(raw)
        except ValidationError as error:
            raise ConfigurationError(f"{path}: {_validation_message(error)}") from None

        return document

    def load(self, path: Path) -> tuple[PreparedPlugin, ...]:
        """Compose data, then validate every plugin before activation."""
        rows: list[_PluginRow] = []
        stack: set[Path] = set()
        documents = 0

        def visit(source: Path) -> None:
            nonlocal documents
            documents += 1
            if documents > 32:
                raise ConfigurationError(
                    "Configuration include document limit exceeded"
                )
            source = source.resolve()
            if source in stack:
                raise ConfigurationError("Configuration include cycle")
            stack.add(source)
            document = self._read(source)
            for include in document.include:
                target = (source.parent / include).resolve()
                if (
                    not include
                    or Path(include).is_absolute()
                    or ".." in Path(include).parts
                    or not target.is_relative_to(source.parent)
                ):
                    raise ConfigurationError("Invalid include path")
                visit(target)
            rows.extend(document.plugins)
            stack.remove(source)

        visit(path)
        prepared: list[PreparedPlugin] = []
        seen: set[str] = set()
        for row in rows:
            if row.name in seen:
                raise ConfigurationError(f"{path}: duplicate plugin {row.name}")
            seen.add(row.name)
            try:
                definition = self.definition(row.name)
            except ConfigurationError as error:
                raise ConfigurationError(f"{path}: {error}") from None
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


def read_yaml(path: Path) -> object:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise ConfigurationError(f"{path}: cannot read UTF-8 configuration") from None
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
        if isinstance(error, yaml.MarkedYAMLError) and error.problem_mark is not None:
            mark = error.problem_mark
            location = f":{mark.line + 1}:{mark.column + 1}"
        # Parser exception strings can contain full source lines, including secrets.
        detail = (
            error.problem
            if isinstance(error, _DataError)
            else "invalid or unsupported YAML"
        )
        raise ConfigurationError(f"{path}{location}: {detail}") from None
    return raw
