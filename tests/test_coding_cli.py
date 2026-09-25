"""An actual CLI process asks approval before an actual file write."""

import json
import os
import pty
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


@pytest.mark.parametrize("reply", ["y", "n", None])
def test_cli_approval_controls_the_write(tmp_path: Path, reply):
    (tmp_path / "code.py").write_text("old\n")
    config = tmp_path / "agent.yaml"
    config.write_text("""version: 1
plugins:
  - name: runtime.langchain
  - name: model.openai
  - name: checkpoint.memory
  - name: approval.policy
  - name: files.editable
  - name: commands.local
    config: {commands: {}}
  - name: tools.coding
""")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            last = body["messages"][-1]
            if last.get("tool_call_id") == "apply":
                message = {"role": "assistant", "content": last["content"]}
                reason = "stop"
            else:
                if last.get("tool_call_id") == "preview":
                    name, ident, args = (
                        "apply_change",
                        "apply",
                        {"change_id": json.loads(last["content"])["change_id"]},
                    )
                else:
                    name, ident, args = (
                        "preview_change",
                        "preview",
                        {"path": "code.py", "content": "new\n"},
                    )
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": ident,
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                    ],
                }
                reason = "tool_calls"
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
    master, slave = pty.openpty()
    try:
        if reply:
            os.write(master, (reply + "\n").encode())
        result = subprocess.run(
            [
                os.environ.get("BRIDGE_AGENT_CLI_PYTHON", sys.executable),
                "-I",
                "-m",
                "bridge_agent.interfaces.agent",
                "--config",
                str(config),
                "--workspace",
                str(tmp_path),
                "--prompt",
                "Change code.py",
            ],
            stdin=slave if reply else subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=20,
            env={
                **os.environ,
                "OPENAI_API_KEY": "test-only",
                "OPENAI_MODEL": "local",
                "OPENAI_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
            },
        )
        assert result.returncode == 0, result.stderr
        assert (tmp_path / "code.py").read_text() == (
            "new\n" if reply == "y" else "old\n"
        )
        if reply:
            assert (
                "Allow?" in result.stderr
                and "-old" in result.stderr
                and "+new" in result.stderr
            )
        else:
            assert "denied" in result.stdout
    finally:
        os.close(master)
        os.close(slave)
        server.shutdown()
        server.server_close()
        thread.join()
