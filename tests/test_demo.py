"""The same consumer works with providers selected by real YAML configurations."""

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ("plugins.upper.yaml", "result: HELLO\n"),
        ("plugins.lower.yaml", "result: hello\n"),
        ("plugins.reverse.yaml", "result: olleH!\n"),
    ],
)
def test_yaml_selects_provider_without_changing_consumer(
    config: str, expected: str
) -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "bridge_agent.interfaces.plugin_demo",
            "--config",
            str(root / "examples" / config),
            "--text",
            "Hello",
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert (result.returncode, result.stdout, result.stderr) == (0, expected, "")


def test_invalid_configuration_exits_nonzero_without_echoing_values(
    tmp_path: Path,
) -> None:
    config = tmp_path / "invalid.yaml"
    config.write_text(
        "version: 1\nplugins:\n  - name: demo.report\n"
        "    config:\n      prefix: [DO_NOT_ECHO_THIS_VALUE]\n"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "bridge_agent.interfaces.plugin_demo",
            "--config",
            str(config),
            "--text",
            "Hello",
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert "demo.report" in result.stderr and "prefix" in result.stderr
    assert "DO_NOT_ECHO_THIS_VALUE" not in result.stderr
