"""The actual Agent loop applies an approved edit and runs a named test command."""

import asyncio
import json
import sys
from pathlib import Path

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from bridge_agent.application.agent import AgentSession
from bridge_agent.bootstrap.agent import agent_catalog
from bridge_agent.contracts.agent import AGENT_RUNTIME
from bridge_agent.contracts.plugins import PluginDefinition
from bridge_agent.kernel.host import PluginHost
from bridge_agent.plugins.langchain.services import MODEL


class CodingModel(BaseChatModel):
    use_skill: bool = False

    @property
    def _llm_type(self):
        return "coding-test"

    def bind_tools(self, tools, **kwargs):
        command = next(tool for tool in tools if tool.name == "run_command")
        assert "test" in command.description
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        last = messages[-1]
        if isinstance(last, ToolMessage):
            data = json.loads(last.content)
            if last.name == "load_skill":
                assert "Fix answer" in last.content
                call = (
                    "preview_change",
                    {"path": "code.py", "content": "answer = 42\n"},
                )
            elif last.name == "preview_change":
                call = ("apply_change", {"change_id": data["change_id"]})
            elif last.name == "apply_change":
                call = ("run_command", {"command": "test", "request_id": "verify"})
            else:
                assert data["exit_code"] == 0
                return ChatResult(
                    generations=[
                        ChatGeneration(
                            message=AIMessage(
                                content="Edit verified: " + data["stdout"]
                            )
                        )
                    ]
                )
        elif self.use_skill:
            call = ("load_skill", {"name": "fix"})
        else:
            call = ("preview_change", {"path": "code.py", "content": "answer = 42\n"})
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
    def __init__(self, use_skill=False):
        self.use_skill = use_skill

    async def activate(self, context):
        context.provide(MODEL, CodingModel(use_skill=self.use_skill))


@pytest.mark.parametrize("use_skill", [False, True])
def test_yaml_agent_edits_and_runs_the_approved_verification(
    tmp_path: Path, use_skill
) -> None:
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "SKILL.md").write_text(
        "---\nname: fix\ndescription: Fix code\n---\nFix answer using preview, apply, and test.\n"
    )
    (tmp_path / "code.py").write_text("answer = 41\n")
    command = [
        sys.executable,
        "-c",
        "from code import answer; assert answer == 42; print('passed')",
    ]
    config = tmp_path / "agent.yaml"
    config.write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": [
                    {"name": "runtime.langchain"},
                    {"name": "model.test"},
                    {"name": "checkpoint.memory"},
                    {
                        "name": "approval.policy",
                        "config": {"write_paths": ["code.py"], "commands": ["test"]},
                    },
                    {"name": "files.editable"},
                    {
                        "name": "commands.local",
                        "config": {"commands": {"test": command}},
                    },
                    *(
                        [{"name": "skills.filesystem", "config": {"roots": ["skills"]}}]
                        if use_skill
                        else []
                    ),
                    {"name": "tools.coding-skills" if use_skill else "tools.coding"},
                ],
            }
        )
    )
    model = PluginDefinition(
        "model.test", lambda config: lambda: ModelPlugin(use_skill), provides=(MODEL,)
    )

    async def scenario() -> None:
        catalog = agent_catalog((model,), workspace=tmp_path, environment={})
        async with PluginHost(catalog.load(config)) as host:
            result = await AgentSession(host.resolve(AGENT_RUNTIME), tmp_path).ask(
                "Fix answer and verify"
            )
            assert result.text == "Edit verified: passed\n"
            assert [tool.name for tool in result.tools] == (
                ["load_skill"] if use_skill else []
            ) + [
                "preview_change",
                "apply_change",
                "run_command",
            ]
            assert (tmp_path / "code.py").read_text() == "answer = 42\n"

    asyncio.run(scenario())
