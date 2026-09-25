"""Skills affect the model context through actual tools and plugin composition."""

import asyncio
import json
from pathlib import Path

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from bridge_agent.application.agent import AgentSession
from bridge_agent.bootstrap.agent import agent_catalog
from bridge_agent.contracts.agent import AGENT_RUNTIME
from bridge_agent.contracts.errors import ConfigurationError
from bridge_agent.contracts.plugins import PluginDefinition
from bridge_agent.kernel.host import PluginHost
from bridge_agent.plugins.langchain.services import MODEL, TOOLS


class SkillModel(BaseChatModel):
    @property
    def _llm_type(self):
        return "skill-test"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        assert "UNSELECTED_BODY" not in str(messages)
        last = messages[-1]
        name = last.name if isinstance(last, ToolMessage) else None
        if name == "read_file":
            data = json.loads(last.content)
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content=f"{data['path']}:1: {data['lines'][0]['text']}"
                        )
                    )
                ]
            )
        if name == "list_skills":
            call = ("load_skill", {"name": "code-map"})
        elif name == "load_skill":
            assert "Read guide.md before source" in last.content
            call = ("read_skill_resource", {"name": "code-map", "path": "guide.md"})
        elif name == "read_skill_resource":
            assert "Read source.py and cite" in last.content
            call = ("read_file", {"path": "source.py"})
        else:
            call = ("list_skills", {})
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content="",
                        tool_calls=[{"id": call[0], "name": call[0], "args": call[1]}],
                    )
                )
            ]
        )


class ModelPlugin:
    async def activate(self, context):
        context.provide(MODEL, SkillModel())


@pytest.mark.parametrize(
    "roots", [[""], ["../outside"], ["/tmp"], ["skills", "skills"]]
)
def test_yaml_rejects_invalid_skill_roots_before_activation(
    tmp_path: Path, roots: list[str]
) -> None:
    path = tmp_path / "skills.yaml"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": [{"name": "skills.filesystem", "config": {"roots": roots}}],
            }
        )
    )
    with pytest.raises(ConfigurationError):
        agent_catalog(workspace=tmp_path, environment={}).load(path)


def test_yaml_skill_plugin_loads_only_selected_content_into_the_agent(
    tmp_path: Path,
) -> None:
    for name, body in (
        ("code-map", "Read guide.md before source"),
        ("unused", "UNSELECTED_BODY"),
    ):
        directory = tmp_path / "skills" / name
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: test\n---\n{body}"
        )
    (tmp_path / "skills/code-map/guide.md").write_text("Read source.py and cite")
    (tmp_path / "source.py").write_text("answer = 42")
    config = tmp_path / "agent.yaml"
    config.write_text("""version: 1
plugins:
  - name: runtime.langchain
  - name: model.test
  - name: checkpoint.memory
  - name: files.local
  - name: skills.filesystem
    config: {roots: [skills]}
  - name: tools.skills
""")
    model = PluginDefinition(
        "model.test", lambda config: ModelPlugin, provides=(MODEL,)
    )

    async def scenario() -> None:
        catalog = agent_catalog((model,), workspace=tmp_path, environment={})
        async with PluginHost(catalog.load(config)) as host:
            assert {tool.name for tool in host.resolve(TOOLS)} == {
                "list_skills",
                "load_skill",
                "read_skill_resource",
                "list_files",
                "read_file",
                "search_files",
            }
            result = await AgentSession(host.resolve(AGENT_RUNTIME), tmp_path).ask(
                "Use code-map"
            )
            assert result.text == "source.py:1: answer = 42"
            assert [tool.name for tool in result.tools] == [
                "list_skills",
                "load_skill",
                "read_skill_resource",
                "read_file",
            ]

    asyncio.run(scenario())
