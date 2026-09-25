"""A real CLI process with a local OpenAI-compatible repository model."""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


@pytest.mark.parametrize("dynamic", [False, True])
@pytest.mark.parametrize("with_skill", [False, True])
def test_cli_reads_only_the_explicit_workspace(
    tmp_path: Path, with_skill: bool, dynamic: bool
) -> None:
    (tmp_path / "code.py").write_text("def answer():\n    return 42\n")
    config = tmp_path / "agent.yaml"
    config.write_text("""version: 1
plugins:
  - name: runtime.langchain
  - name: model.openai
  - name: checkpoint.memory
  - name: files.local
  - name: tools.workspace
""")
    if with_skill:
        directory = tmp_path / "skills" / "code-map"
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            "---\nname: code-map\ndescription: test\n---\nRead code.py and cite"
        )
        config.write_text(
            config.read_text().replace(
                "  - name: tools.workspace",
                "  - name: skills.filesystem\n    config: {roots: [skills]}\n  - name: tools.skills",
            )
        )
    if dynamic:
        import yaml

        document = yaml.safe_load(config.read_text())
        config.write_text(
            json.dumps(
                {
                    "version": 2,
                    "entries": [
                        {"id": f"plugin-{index}", **row}
                        for index, row in enumerate(document["plugins"])
                    ],
                }
            )
        )
    observed_skills = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            last = body["messages"][-1]
            if last.get("tool_call_id") == "skill-call":
                observed_skills.append(json.loads(last["content"])["content"])
            if last.get("tool_call_id") == "file-call":
                message = {"role": "assistant", "content": last["content"]}
                reason = "stop"
            else:
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "file-call",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path":"code.py"}',
                            },
                        }
                    ],
                }
                reason = "tool_calls"
                if with_skill and last["role"] != "tool":
                    message["tool_calls"][0] = {
                        "id": "skill-call",
                        "type": "function",
                        "function": {
                            "name": "load_skill",
                            "arguments": '{"name":"code-map"}',
                        },
                    }
            data = json.dumps(
                {
                    "id": "local",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "local",
                    "choices": [
                        {"index": 0, "finish_reason": reason, "message": message}
                    ],
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        environment = {
            **os.environ,
            "OPENAI_API_KEY": "test-only",
            "OPENAI_MODEL": "local",
            "OPENAI_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
        }
        result = subprocess.run(
            [
                os.environ.get("BRIDGE_AGENT_CLI_PYTHON", sys.executable),
                "-I",
                "-m",
                "bridge_agent.interfaces.agent",
                *(["--dynamic"] if dynamic else []),
                "--config",
                str(config),
                "--workspace",
                str(tmp_path),
                "--prompt",
                "Read code.py",
            ],
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == 0, result.stderr
        answer = json.loads(result.stdout)
        assert answer["path"] == "code.py"
        assert answer["lines"][1] == {"number": 2, "text": "    return 42"}
        if with_skill:
            assert observed_skills == ["Read code.py and cite"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
