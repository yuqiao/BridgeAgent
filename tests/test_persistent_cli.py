"""Independent CLI processes resume persisted checkpoint history."""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

pytest_plugins = ["tests.test_openai_adapter"]


def test_killed_process_leaves_an_incomplete_session_that_is_not_replayed(
    tmp_path: Path,
) -> None:
    entered, release = threading.Event(), threading.Event()
    calls = []

    class WaitingHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            calls.append(1)
            entered.set()
            release.wait(20)

    server = ThreadingHTTPServer(("127.0.0.1", 0), WaitingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = tmp_path / "agent.yaml"
    config.write_text(f"""version: 1
plugins:
  - name: runtime.langchain
  - name: model.openai
  - name: tools.arithmetic
  - name: checkpoint.sqlite
    config: {{path: '{tmp_path / "sessions.sqlite"}'}}
""")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("OPENAI_", "LANGCHAIN_", "LANGSMITH_"))
    }
    environment.update(
        OPENAI_BASE_URL=f"http://127.0.0.1:{server.server_port}/v1",
        OPENAI_MODEL="local",
        OPENAI_API_KEY="test-only",
    )
    command = [
        os.environ.get("BRIDGE_AGENT_CLI_PYTHON", sys.executable),
        "-I",
        "-m",
        "bridge_agent.interfaces.agent",
        "--config",
        str(config),
        "--workspace",
        str(tmp_path),
        "--session-id",
        "interrupted",
    ]
    process = subprocess.Popen(
        [*command, "--prompt", "wait"],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert entered.wait(10)
        competing = subprocess.run(
            [*command, "--session-status"],
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert competing.returncode == 1
        assert "already in use" in competing.stderr
        process.kill()
        process.communicate(timeout=5)
        status = subprocess.run(
            [*command, "--session-status"],
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert status.returncode == 0, status.stderr
        assert json.loads(status.stdout)["status"] == "incomplete"
        result = subprocess.run(
            [*command, "--prompt", "continue"],
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 1
        assert "incomplete" in result.stderr
        assert len(calls) == 1
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
        release.set()
        server.shutdown()
        server.server_close()
        thread.join()


def test_cli_resumes_a_session_in_a_fresh_process(
    tmp_path: Path, model_endpoint
) -> None:
    url, requests = model_endpoint
    config = tmp_path / "agent.yaml"
    config.write_text(f"""version: 1
plugins:
  - name: runtime.langchain
  - name: model.openai
  - name: tools.arithmetic
  - name: checkpoint.sqlite
    config: {{path: '{tmp_path / "sessions.sqlite"}'}}
""")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("OPENAI_", "LANGCHAIN_", "LANGSMITH_"))
    }
    environment.update(
        OPENAI_BASE_URL=url, OPENAI_MODEL="local", OPENAI_API_KEY="test-only"
    )
    command = [
        os.environ.get("BRIDGE_AGENT_CLI_PYTHON", sys.executable),
        "-I",
        "-m",
        "bridge_agent.interfaces.agent",
        "--config",
        str(config),
        "--workspace",
        str(tmp_path),
        "--session-id",
        "saved",
    ]
    for text in ("first", "second"):
        result = subprocess.run(
            [*command, "--prompt", text],
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, result.stderr
        assert "42" in result.stdout
    assert [
        message["content"]
        for message in requests[2][1]["messages"]
        if message["role"] == "user"
    ] == ["first", "second"]
    count = len(requests)
    status = subprocess.run(
        [*command, "--session-status"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["status"] == "succeeded"
    assert len(requests) == count
