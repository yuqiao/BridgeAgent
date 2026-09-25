"""Entry points are an external boundary; loading is always opt-in."""

import asyncio

from bridge_agent.bootstrap.agent import agent_catalog
from bridge_agent.contracts.agent import AGENT_RUNTIME, RunRequest
from bridge_agent.kernel.host import PluginHost


def test_selected_installed_package_runs_without_core_changes(tmp_path):
    config = tmp_path / "agent.yaml"
    config.write_text("version: 1\nplugins: [{name: runtime.example}]\n")

    async def scenario():
        async with PluginHost(agent_catalog(environment={}).load(config)) as host:
            result = await host.resolve(AGENT_RUNTIME).run(
                RunRequest(session_id="external", workspace=tmp_path, text="hello")
            )
            assert result.text == "external: hello"

    asyncio.run(scenario())


def test_unselected_broken_entry_point_is_not_imported(tmp_path, monkeypatch):
    import importlib.metadata
    from importlib.metadata import EntryPoint

    monkeypatch.setattr(
        importlib.metadata,
        "entry_points",
        lambda **kwargs: (
            EntryPoint(
                name="unused",
                value="missing_package:plugin",
                group="bridge_agent.plugins",
            ),
        ),
    )
    path = tmp_path / "agent.yaml"
    path.write_text("version: 1\nplugins: [{name: checkpoint.memory}]\n")
    assert agent_catalog(environment={}).load(path)[0].name == "checkpoint.memory"


def test_external_cannot_silently_shadow_a_builtin(tmp_path, monkeypatch):
    import importlib.metadata
    from importlib.metadata import EntryPoint

    import pytest

    from bridge_agent.contracts.errors import ConfigurationError

    monkeypatch.setattr(
        importlib.metadata,
        "entry_points",
        lambda **kwargs: (
            EntryPoint(
                name="checkpoint.memory",
                value="missing_package:plugin",
                group="bridge_agent.plugins",
            ),
        ),
    )
    path = tmp_path / "agent.yaml"
    path.write_text("version: 1\nplugins: [{name: checkpoint.memory}]\n")
    with pytest.raises(ConfigurationError, match="conflict"):
        agent_catalog(environment={}).load(path)


def test_incompatible_export_and_duplicate_packages_fail_clearly(tmp_path, monkeypatch):
    import importlib.metadata

    import pytest

    from bridge_agent.contracts.errors import ConfigurationError
    from bridge_agent.contracts.packages import PluginExport
    from bridge_agent.contracts.plugins import PluginDefinition

    class Candidate:
        name = "runtime.external"

        def load(self):
            return PluginExport(99, PluginDefinition(self.name, lambda config: None))

    path = tmp_path / "agent.yaml"
    path.write_text("version: 1\nplugins: [{name: runtime.external}]\n")
    monkeypatch.setattr(
        importlib.metadata, "entry_points", lambda **kwargs: [Candidate()]
    )
    with pytest.raises(ConfigurationError, match="Incompatible"):
        agent_catalog(environment={}).load(path)
    monkeypatch.setattr(
        importlib.metadata, "entry_points", lambda **kwargs: [Candidate(), Candidate()]
    )
    with pytest.raises(ConfigurationError, match="Ambiguous"):
        agent_catalog(environment={}).load(path)


def test_external_runtime_works_in_a_cli_process(tmp_path):
    import os
    import subprocess
    import sys

    config = tmp_path / "agent.yaml"
    config.write_text("version: 1\nplugins: [{name: runtime.example}]\n")
    result = subprocess.run(
        [
            os.environ.get("BRIDGE_AGENT_CLI_PYTHON", sys.executable),
            "-I",
            "-m",
            "bridge_agent.interfaces.agent",
            "--config",
            str(config),
            "--workspace",
            str(tmp_path),
            "--prompt",
            "hello",
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "external: hello\n"
