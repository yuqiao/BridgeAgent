"""Use the installed CLI and real HTTP adapter with a local model endpoint."""

import os
import signal
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytest_plugins = ["tests.test_openai_adapter"]
CLI_PYTHON = os.environ.get("BRIDGE_AGENT_CLI_PYTHON", sys.executable)


def test_cli_sigint_cancels_a_running_model_request(tmp_path: Path) -> None:
    entered, release = threading.Event(), threading.Event()

    class WaitingHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            entered.set()
            release.wait(15)

    server = ThreadingHTTPServer(("127.0.0.1", 0), WaitingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("OPENAI_", "LANGSMITH_", "LANGCHAIN_"))
    }
    environment.update(
        OPENAI_BASE_URL=f"http://127.0.0.1:{server.server_port}/v1",
        OPENAI_API_KEY="test-only",
        OPENAI_MODEL="local-model",
    )
    config = Path(__file__).resolve().parents[1] / "examples/agent.yaml"
    process = subprocess.Popen(
        [
            CLI_PYTHON,
            "-I",
            "-m",
            "bridge_agent.interfaces.agent",
            "--config",
            str(config),
            "--workspace",
            str(tmp_path),
            "--prompt",
            "wait",
        ],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert entered.wait(10), "Model request did not start"
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 130, stderr
        assert stdout == ""
        assert stderr.strip() == "Interrupted"
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
        release.set()
        server.shutdown()
        server.server_close()
        thread.join()


def test_cli_runs_a_single_task_from_explicit_env_file(
    tmp_path: Path, model_endpoint
) -> None:
    url, _ = model_endpoint
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"OPENAI_BASE_URL={url}\nOPENAI_API_KEY=test-only\nOPENAI_MODEL=local-model\n"
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("OPENAI_", "LANGSMITH_", "LANGCHAIN_"))
    }
    environment["OPENAI_BASE_URL"] = "http://127.0.0.1:1/incorrect"
    config = Path(__file__).resolve().parents[1] / "examples/agent.yaml"
    result = subprocess.run(
        [
            CLI_PYTHON,
            "-I",
            "-m",
            "bridge_agent.interfaces.agent",
            "--config",
            str(config),
            "--env-file",
            str(env_file),
            "--env-override",
            "--workspace",
            str(tmp_path),
            "--prompt",
            "Add 19 and 23",
            "--show-tools",
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "answer: 42\n"
    assert "add: 42" in result.stderr
    assert "test-only" not in result.stderr


def test_cli_missing_model_configuration_is_safe_and_nonzero(tmp_path: Path) -> None:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("OPENAI_", "LANGSMITH_", "LANGCHAIN_"))
    }
    environment["OPENAI_API_KEY"] = "PRIVATE_CREDENTIAL"
    config = Path(__file__).resolve().parents[1] / "examples/agent.yaml"
    result = subprocess.run(
        [
            CLI_PYTHON,
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
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert "model.openai" in result.stderr
    assert "PRIVATE_CREDENTIAL" not in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("ending", ["/exit\n", ""])
def test_cli_serial_turns_share_history_and_new_starts_another_session(
    tmp_path: Path, model_endpoint, ending: str
) -> None:
    url, requests = model_endpoint
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("OPENAI_", "LANGSMITH_", "LANGCHAIN_"))
    }
    environment.update(
        OPENAI_BASE_URL=url, OPENAI_API_KEY="test-only", OPENAI_MODEL="local-model"
    )
    config = Path(__file__).resolve().parents[1] / "examples/agent.yaml"
    result = subprocess.run(
        [
            CLI_PYTHON,
            "-I",
            "-m",
            "bridge_agent.interfaces.agent",
            "--config",
            str(config),
            "--workspace",
            str(tmp_path),
        ],
        env=environment,
        input="first\nsecond\n/new\nthird\n" + ending,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "answer: 42\nanswer: 42\nanswer: 42\n"
    user_turns = [
        [m["content"] for m in body["messages"] if m["role"] == "user"]
        for _, body in requests
    ]
    assert user_turns == [
        ["first"],
        ["first"],
        ["first", "second"],
        ["first", "second"],
        ["third"],
        ["third"],
    ]
