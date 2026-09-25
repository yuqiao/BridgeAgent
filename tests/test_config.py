"""Configuration behavior through the public catalog boundary."""

from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from bridge_agent.bootstrap.config import PluginCatalog
from bridge_agent.bootstrap.demo import demo_catalog
from bridge_agent.contracts.errors import ConfigurationError
from bridge_agent.contracts.plugins import Plugin, PluginDefinition


def test_duplicate_yaml_key_reports_the_problem_and_location(tmp_path: Path) -> None:
    config = tmp_path / "duplicate.yaml"
    config.write_text("version: 1\nplugins: []\nplugins: []\n")
    with pytest.raises(ConfigurationError) as failure:
        demo_catalog().load(config)
    assert f"{config}:3:1: duplicate mapping key" in str(failure.value)


@pytest.mark.parametrize(
    "source",
    [
        "version: 1\nplugins: [",
        "version: true\nplugins: []",
        "version: 2\nplugins: []",
        "version: 1\nplugins: []\nunknown: 1",
        "version: 1\nplugins: [{name: os.system}]",
        "version: 1\nplugins: [{name: demo.uppercase}, {name: demo.uppercase}]",
        "version: 1\nplugins: [{name: demo.report, config: {prefix: 42}}]",
        "version: 1\nplugins: [{name: demo.report, config: {prefix: yes}}]",
        "version: 1\nplugins: [{name: demo.uppercase, config: {unknown: 1}}]",
        "version: 1\nplugins: []\n---\nversion: 1\nplugins: []",
        "version: 1\nplugins: &items []",
        "version: 1\nplugins: *items",
        "version: 1\nplugins: [{name: demo.report, config: {<<: {prefix: x}}}]",
        "version: 1\nplugins: !!python/object:builtins.object {}",
    ],
)
def test_invalid_configuration_is_rejected_by_catalog(
    tmp_path: Path, source: str
) -> None:
    config = tmp_path / "invalid.yaml"
    config.write_text(source)
    with pytest.raises(ConfigurationError, match="invalid.yaml"):
        demo_catalog().load(config)


@pytest.mark.parametrize(
    "scalar",
    ["!!int PRIVATE_NOT_AN_INTEGER", "!!int ''", "!!bool PRIVATE_NOT_A_BOOLEAN"],
)
def test_invalid_explicit_scalar_has_a_config_error_without_exposing_value(
    tmp_path: Path, scalar: str
) -> None:
    config = tmp_path / "scalar.yaml"
    config.write_text(f"version: {scalar}\nplugins: []\n")
    with pytest.raises(ConfigurationError) as failure:
        demo_catalog().load(config)
    assert f"{config}:1:10:" in str(failure.value)
    assert "PRIVATE_" not in str(failure.value)


def test_all_configuration_is_validated_before_any_factory_runs(tmp_path: Path) -> None:
    constructed: list[str] = []

    def prepare(config: Mapping[str, object]) -> Callable[[], Plugin]:
        def factory() -> Plugin:
            constructed.append("first")
            raise AssertionError("must not construct before config validation")

        return factory

    catalog = PluginCatalog((PluginDefinition("first", prepare),))
    config = tmp_path / "late-invalid.yaml"
    config.write_text("version: 1\nplugins: [{name: first}, {name: unknown}]\n")
    with pytest.raises(ConfigurationError, match="unknown plugin"):
        catalog.load(config)
    assert constructed == []
