"""Configuration composition remains data-only and rejects ambiguous overrides."""

from bridge_agent.bootstrap.agent import agent_catalog


def test_includes_compose_plugins_in_declared_order(tmp_path):
    (tmp_path / "base.yaml").write_text(
        "version: 1\nplugins: [{name: checkpoint.memory}]\n"
    )
    (tmp_path / "agent.yaml").write_text(
        "version: 1\ninclude: [base.yaml]\nplugins: [{name: tools.arithmetic}]\n"
    )
    prepared = agent_catalog(environment={}).load(tmp_path / "agent.yaml")
    assert [item.name for item in prepared] == ["checkpoint.memory", "tools.arithmetic"]


def test_cyclic_include_is_a_configuration_error(tmp_path):
    import pytest

    from bridge_agent.contracts.errors import ConfigurationError

    path = tmp_path / "cycle.yaml"
    path.write_text("version: 1\ninclude: [cycle.yaml]\n")
    with pytest.raises(ConfigurationError, match="cycle"):
        agent_catalog(environment={}).load(path)


def test_include_cannot_escape_its_configuration_directory(tmp_path):
    import pytest

    from bridge_agent.contracts.errors import ConfigurationError

    (tmp_path / "outside.yaml").write_text("version: 1\nplugins: []\n")
    (tmp_path / "child").mkdir()
    path = tmp_path / "child" / "agent.yaml"
    path.write_text("version: 1\ninclude: [../outside.yaml]\n")
    with pytest.raises(ConfigurationError, match="include path"):
        agent_catalog(environment={}).load(path)


def test_composition_rejects_duplicate_plugins(tmp_path):
    import pytest

    from bridge_agent.contracts.errors import ConfigurationError

    (tmp_path / "base.yaml").write_text(
        "version: 1\nplugins: [{name: checkpoint.memory}]\n"
    )
    (tmp_path / "agent.yaml").write_text(
        "version: 1\ninclude: [base.yaml]\nplugins: [{name: checkpoint.memory}]\n"
    )
    with pytest.raises(ConfigurationError, match="duplicate plugin"):
        agent_catalog(environment={}).load(tmp_path / "agent.yaml")


def test_composition_has_a_document_budget(tmp_path):
    import pytest

    from bridge_agent.contracts.errors import ConfigurationError

    for i in range(40):
        (tmp_path / f"{i}.yaml").write_text(
            f"version: 1\ninclude: [{i + 1}.yaml]\n" if i < 39 else "version: 1\n"
        )
    with pytest.raises(ConfigurationError, match="limit"):
        agent_catalog(environment={}).load(tmp_path / "0.yaml")
