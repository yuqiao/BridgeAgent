"""Action providers exercised through the real YAML host."""

import asyncio
import json
from pathlib import Path

import pytest

from bridge_agent.bootstrap.agent import agent_catalog
from bridge_agent.contracts.actions import COMMANDS
from bridge_agent.contracts.errors import ActionError, ConfigurationError
from bridge_agent.kernel.host import PluginHost


def config(tmp_path, commands):
    path = tmp_path / "actions.yaml"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": [
                    {"name": "approval.policy"},
                    {"name": "commands.local", "config": {"commands": commands}},
                ],
            }
        )
    )
    return path


def test_closed_host_cannot_start_a_command(tmp_path: Path) -> None:
    async def scenario():
        async with PluginHost(
            agent_catalog(workspace=tmp_path).load(config(tmp_path, {"test": ["true"]}))
        ) as host:
            commands = host.resolve(COMMANDS)
        with pytest.raises(ActionError, match="closed"):
            await commands.run("test", "after-close")

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "commands", [{"test": []}, {"": ["true"]}, {"test": ["bad\x00arg"]}]
)
def test_invalid_command_templates_fail_before_activation(
    tmp_path: Path, commands
) -> None:
    with pytest.raises(ConfigurationError):
        agent_catalog(workspace=tmp_path).load(config(tmp_path, commands))
