"""The installed CLI runs dynamic configuration and exposes inspect/reload."""

import json
import os
import subprocess
import sys


def test_dynamic_cli_runs_and_inspects_an_external_runtime(tmp_path):
    path = tmp_path / "dynamic.yaml"
    path.write_text("version: 2\nentries: [{id: runtime, name: runtime.example}]\n")
    result = subprocess.run(
        [
            os.environ.get("BRIDGE_AGENT_CLI_PYTHON", sys.executable),
            "-I",
            "-m",
            "bridge_agent.interfaces.agent",
            "--dynamic",
            "--config",
            str(path),
            "--workspace",
            str(tmp_path),
        ],
        input="/plugins\nhello\n/reload\nagain\n/exit\n",
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert json.loads(lines[0])[0]["state"] == "active"
    assert lines[1:] == ["external: hello", "external: again"]
    assert "Reloaded" in result.stderr


def test_watch_applies_configuration_while_cli_waits_for_input(tmp_path):
    import queue
    import threading

    path = tmp_path / "dynamic.yaml"
    path.write_text("version: 2\nentries: [{id: runtime, name: runtime.example}]\n")
    process = subprocess.Popen(
        [
            os.environ.get("BRIDGE_AGENT_CLI_PYTHON", sys.executable),
            "-I",
            "-m",
            "bridge_agent.interfaces.agent",
            "--dynamic",
            "--watch",
            "--config",
            str(path),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    errors = queue.Queue()
    output = queue.Queue()

    def lines(stream, target):
        for line in stream:
            target.put(line.rstrip())

    readers = [
        threading.Thread(target=lines, args=(process.stdout, output), daemon=True),
        threading.Thread(target=lines, args=(process.stderr, errors), daemon=True),
    ]
    for reader in readers:
        reader.start()
    try:
        process.stdin.write("hello\n")
        process.stdin.flush()
        assert output.get(timeout=10) == "external: hello"
        path.write_text(
            'version: 2\nentries: [{id: runtime, name: runtime.example, config: {prefix: "updated: "}}]\n'
        )
        assert errors.get(timeout=10) == "Reloaded"
        process.stdin.write("again\n/exit\n")
        process.stdin.flush()
        assert output.get(timeout=10) == "updated: again"
        assert process.wait(timeout=10) == 0
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        for reader in readers:
            reader.join(timeout=2)
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()


def test_cli_can_select_an_isolated_runtime_entry(tmp_path):
    path = tmp_path / "dynamic.yaml"
    path.write_text("""version: 2
entries:
  - id: root
    name: runtime.example
  - id: child
    name: runtime.example
    scope: {agent.runtime: child}
    config: {prefix: "child: "}
""")
    result = subprocess.run(
        [
            os.environ.get("BRIDGE_AGENT_CLI_PYTHON", sys.executable),
            "-I",
            "-m",
            "bridge_agent.interfaces.agent",
            "--dynamic",
            "--entry",
            "child",
            "--config",
            str(path),
            "--prompt",
            "hello",
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "child: hello\n"
