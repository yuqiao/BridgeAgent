"""Check the promised type contract as a plugin author would consume it."""

import subprocess
import sys
from pathlib import Path


def test_service_registration_rejects_value_of_wrong_type(tmp_path: Path) -> None:
    source = tmp_path / "wrong_provider.py"
    source.write_text(
        "from bridge_agent.contracts.plugins import PluginContext, ServiceKey\n"
        "TEXT = ServiceKey[str]('text')\n"
        "def register(context: PluginContext) -> None:\n"
        "    context.provide(TEXT, 123)\n"
    )
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", str(source)],
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "wrong_provider.py:4:" in result.stdout


def test_service_consumers_receive_the_keys_declared_type(tmp_path: Path) -> None:
    source = tmp_path / "consumer.py"
    source.write_text(
        "from typing import assert_type\n"
        "from bridge_agent.contracts.plugins import PluginContext, ServiceKey\n"
        "from bridge_agent.kernel.host import PluginHost\n"
        "TEXT = ServiceKey[str]('text')\n"
        "def consume(context: PluginContext, host: PluginHost) -> None:\n"
        "    assert_type(context.require(TEXT), str)\n"
        "    assert_type(host.resolve(TEXT), str)\n"
    )
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", str(source)],
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
