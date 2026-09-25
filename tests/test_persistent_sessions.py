"""Persistence is observed through Agent runs across fresh plugin hosts."""

import asyncio
from pathlib import Path

import pytest

from bridge_agent.application.agent import AgentSession
from bridge_agent.bootstrap.agent import agent_catalog
from bridge_agent.contracts.agent import AGENT_RUNTIME
from bridge_agent.contracts.errors import AgentExecutionError, AgentSessionError
from bridge_agent.contracts.plugins import PluginDefinition
from bridge_agent.kernel.host import PluginHost
from bridge_agent.plugins.langchain.services import MODEL
from tests.test_agent_runtime import MemoryModel


class RecoverableModel(MemoryModel):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if messages[-1].text == "fail":
            raise RuntimeError("model unavailable")
        return super()._generate(messages, stop, run_manager, **kwargs)


class ModelPlugin:
    async def activate(self, context):
        context.provide(MODEL, RecoverableModel())


MODEL_PLUGIN = PluginDefinition(
    "model.test", lambda config: ModelPlugin, provides=(MODEL,)
)


def configuration(tmp_path: Path) -> Path:
    path = tmp_path / "agent.yaml"
    path.write_text(f"""version: 1
plugins:
  - name: runtime.langchain
  - name: model.test
  - name: tools.arithmetic
  - name: checkpoint.sqlite
    config:
      path: {tmp_path / "sessions.sqlite"}
""")
    return path


def test_completed_session_survives_closing_and_reopening_the_host(
    tmp_path: Path,
) -> None:
    config = configuration(tmp_path)

    async def ask(text: str):
        catalog = agent_catalog((MODEL_PLUGIN,), workspace=tmp_path, environment={})
        async with PluginHost(catalog.load(config)) as host:
            return await AgentSession(
                host.resolve(AGENT_RUNTIME), tmp_path, session_id="saved"
            ).ask(text)

    assert "first" in asyncio.run(ask("first")).text
    result = asyncio.run(ask("second"))
    assert "first" in result.text and "second" in result.text


def test_saved_session_cannot_be_reopened_in_another_workspace(tmp_path: Path) -> None:
    config = configuration(tmp_path)
    other = tmp_path / "other"
    other.mkdir()

    async def ask(workspace: Path):
        catalog = agent_catalog((MODEL_PLUGIN,), workspace=workspace, environment={})
        async with PluginHost(catalog.load(config)) as host:
            return await AgentSession(
                host.resolve(AGENT_RUNTIME), workspace, session_id="saved"
            ).ask("hello")

    asyncio.run(ask(tmp_path))
    with pytest.raises(AgentSessionError, match="workspace"):
        asyncio.run(ask(other))


def test_failed_session_is_still_incomplete_after_restarting(tmp_path: Path) -> None:
    config = configuration(tmp_path)

    async def ask(text: str):
        catalog = agent_catalog((MODEL_PLUGIN,), workspace=tmp_path, environment={})
        async with PluginHost(catalog.load(config)) as host:
            return await AgentSession(
                host.resolve(AGENT_RUNTIME), tmp_path, session_id="failed"
            ).ask(text)

    with pytest.raises(AgentExecutionError):
        asyncio.run(ask("fail"))
    with pytest.raises(AgentSessionError, match="incomplete"):
        asyncio.run(ask("continue"))


def test_session_status_is_available_after_reopening_without_a_model_call(
    tmp_path: Path,
) -> None:
    from bridge_agent.contracts.sessions import SESSION_CONTROL

    config = configuration(tmp_path)

    async def scenario() -> None:
        catalog = agent_catalog((MODEL_PLUGIN,), workspace=tmp_path, environment={})
        async with PluginHost(catalog.load(config)) as host:
            assert await host.resolve(SESSION_CONTROL).get_session("saved") is None
            await AgentSession(
                host.resolve(AGENT_RUNTIME), tmp_path, session_id="saved"
            ).ask("first")
        async with PluginHost(catalog.load(config)) as host:
            info = await host.resolve(SESSION_CONTROL).get_session("saved")
            assert info.session_id == "saved"
            assert info.workspace == tmp_path.resolve()
            assert info.status == "succeeded"

    asyncio.run(scenario())


def test_changed_runtime_configuration_cannot_silently_resume_old_history(
    tmp_path: Path,
) -> None:
    config = configuration(tmp_path)

    async def ask():
        catalog = agent_catalog((MODEL_PLUGIN,), workspace=tmp_path, environment={})
        async with PluginHost(catalog.load(config)) as host:
            return await AgentSession(
                host.resolve(AGENT_RUNTIME), tmp_path, session_id="saved"
            ).ask("hello")

    asyncio.run(ask())
    config.write_text(
        config.read_text().replace(
            "  - name: runtime.langchain",
            "  - name: runtime.langchain\n    config: {system_prompt: Different behavior}",
        )
    )
    with pytest.raises(AgentSessionError, match="compatible"):
        asyncio.run(ask())

    async def inspect() -> None:
        from bridge_agent.contracts.sessions import SESSION_CONTROL

        catalog = agent_catalog((MODEL_PLUGIN,), workspace=tmp_path, environment={})
        async with PluginHost(catalog.load(config)) as host:
            info = await host.resolve(SESSION_CONTROL).get_session("saved")
            assert info.status == "succeeded"
            assert not info.compatible

    asyncio.run(inspect())


def test_database_is_owned_by_one_host_until_shutdown(tmp_path: Path) -> None:
    from bridge_agent.contracts.errors import PluginActivationError

    config = configuration(tmp_path)

    async def scenario() -> None:
        catalog = agent_catalog((MODEL_PLUGIN,), workspace=tmp_path, environment={})
        async with PluginHost(catalog.load(config)):
            with pytest.raises(PluginActivationError, match="checkpoint.sqlite"):
                async with PluginHost(catalog.load(config)):
                    pass
        async with PluginHost(catalog.load(config)) as host:
            assert host.resolve(AGENT_RUNTIME) is not None

    asyncio.run(scenario())


def test_invalid_database_target_reports_a_safe_configuration_cause(
    tmp_path: Path,
) -> None:
    from bridge_agent.contracts.errors import ConfigurationError, PluginActivationError

    config = configuration(tmp_path)
    config.write_text(
        config.read_text().replace(str(tmp_path / "sessions.sqlite"), str(tmp_path))
    )

    async def scenario() -> None:
        catalog = agent_catalog((MODEL_PLUGIN,), workspace=tmp_path, environment={})
        with pytest.raises(PluginActivationError) as raised:
            async with PluginHost(catalog.load(config)):
                pass
        assert isinstance(raised.value.__cause__, ConfigurationError)

    asyncio.run(scenario())
