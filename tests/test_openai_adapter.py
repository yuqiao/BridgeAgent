"""Real ChatOpenAI HTTP integration with a local, deterministic model endpoint."""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from bridge_agent.application.agent import AgentSession
from bridge_agent.bootstrap.agent import agent_catalog
from bridge_agent.contracts.agent import AGENT_RUNTIME
from bridge_agent.kernel.host import PluginHost


@pytest.fixture
def model_endpoint():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, body))
            if body["messages"][-1]["role"] == "tool":
                message = {
                    "role": "assistant",
                    "content": "answer: " + body["messages"][-1]["content"],
                }
                reason = "stop"
            else:
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-add",
                            "type": "function",
                            "function": {"name": "add", "arguments": '{"a":19,"b":23}'},
                        }
                    ],
                }
                reason = "tool_calls"
            data = json.dumps(
                {
                    "id": "chat-local",
                    "object": "chat.completion",
                    "created": 1,
                    "model": body["model"],
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
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_openai_model_plugin_uses_chat_completions_and_returns_tool_results(
    tmp_path: Path, model_endpoint
) -> None:
    url, requests = model_endpoint
    path = tmp_path / "agent.yaml"
    path.write_text("""version: 1
plugins:
  - name: runtime.langchain
  - name: tools.arithmetic
  - name: checkpoint.memory
  - name: model.openai
""")

    async def scenario() -> None:
        catalog = agent_catalog(
            environment={
                "OPENAI_BASE_URL": url,
                "OPENAI_API_KEY": "local-test-only",
                "OPENAI_MODEL": "local-model",
            }
        )
        async with PluginHost(catalog.load(path)) as host:
            result = await AgentSession(host.resolve(AGENT_RUNTIME), tmp_path).ask(
                "Add 19 and 23"
            )
            assert result.text == "answer: 42"
            assert result.tools[0].content == "42"

    asyncio.run(scenario())
    assert [path for path, _ in requests] == [
        "/v1/chat/completions",
        "/v1/chat/completions",
    ]
    assert all(body["model"] == "local-model" for _, body in requests)
    assert requests[1][1]["messages"][-1]["tool_call_id"] == "call-add"


def test_model_http_resources_close_with_the_host(tmp_path: Path) -> None:
    from bridge_agent.plugins.langchain.services import MODEL

    path = tmp_path / "model.yaml"
    path.write_text("version: 1\nplugins: [{name: model.openai}]\n")

    async def scenario() -> None:
        catalog = agent_catalog(
            environment={
                "OPENAI_BASE_URL": "http://127.0.0.1:1/v1",
                "OPENAI_API_KEY": "test-only",
                "OPENAI_MODEL": "local-model",
            }
        )
        async with PluginHost(catalog.load(path)) as host:
            model = host.resolve(MODEL)
            assert not model.http_client.is_closed
            assert not model.http_async_client.is_closed
        assert model.http_client.is_closed
        assert model.http_async_client.is_closed

    asyncio.run(scenario())
