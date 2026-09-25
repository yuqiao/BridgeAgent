"""Real child processes exercised through the approved command capability."""

import asyncio
import sys
from pathlib import Path

import pytest

from bridge_agent.contracts.errors import ActionError
from bridge_agent.plugins.commands import LocalCommandRunner


class Allow:
    async def confirm(self, request):
        return True


def test_named_command_returns_output_and_exit_status(tmp_path: Path) -> None:
    runner = LocalCommandRunner(
        tmp_path, {"test": (sys.executable, "-c", "print('passed')")}, Allow()
    )
    result = asyncio.run(runner.run("test", "run-1"))
    assert result.exit_code == 0
    assert result.stdout == "passed\n"
    assert result.stderr == ""


def test_command_requires_approval_before_starting_a_process(tmp_path: Path) -> None:
    class Deny:
        async def confirm(self, request):
            assert request.kind == "command"
            assert request.workspace == tmp_path.resolve()
            assert "marker" in request.details
            return False

    runner = LocalCommandRunner(
        tmp_path,
        {"test": (sys.executable, "-c", "open('marker', 'w').write('ran')")},
        Deny(),
    )
    with pytest.raises(ActionError, match="denied"):
        asyncio.run(runner.run("test", "denied"))
    assert not (tmp_path / "marker").exists()


def test_commands_do_not_inherit_model_credentials(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "private-value")
    runner = LocalCommandRunner(
        tmp_path,
        {
            "test": (
                sys.executable,
                "-c",
                "import os; print(os.environ.get('OPENAI_API_KEY', 'absent'))",
            )
        },
        Allow(),
    )
    assert asyncio.run(runner.run("test", "environment")).stdout == "absent\n"


def test_command_output_is_bounded_and_marked_truncated(tmp_path: Path) -> None:
    runner = LocalCommandRunner(
        tmp_path, {"test": (sys.executable, "-c", "print('x' * 100000)")}, Allow()
    )
    result = asyncio.run(runner.run("test", "large"))
    assert len(result.stdout.encode()) + len(result.stderr.encode()) <= 65536
    assert result.truncated


def test_command_timeout_stops_the_process(tmp_path: Path) -> None:
    runner = LocalCommandRunner(
        tmp_path,
        {"test": (sys.executable, "-c", "import time; time.sleep(30)")},
        Allow(),
        timeout_seconds=0.05,
    )
    with pytest.raises(ActionError, match="timed out"):
        asyncio.run(runner.run("test", "timeout"))


def test_completed_request_id_is_not_executed_again(tmp_path: Path) -> None:
    runner = LocalCommandRunner(
        tmp_path,
        {
            "test": (
                sys.executable,
                "-c",
                "with open('counter', 'a') as f: f.write('x')",
            )
        },
        Allow(),
    )

    async def scenario() -> None:
        first = await runner.run("test", "once")
        second = await runner.run("test", "once")
        assert first == second
        assert (tmp_path / "counter").read_text() == "x"

    asyncio.run(scenario())


def test_cancel_waits_for_the_child_to_exit_and_prevents_implicit_retry(
    tmp_path: Path,
) -> None:
    import os

    async def scenario() -> None:
        started = asyncio.Event()
        pids = []

        async def connected(reader, writer):
            pids.append(int(await reader.readline()))
            started.set()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(connected, "127.0.0.1", 0)
        async with server:
            port = server.sockets[0].getsockname()[1]
            script = f"import socket,os,time; s=socket.create_connection(('127.0.0.1',{port})); s.sendall((str(os.getpid())+'\\n').encode()); time.sleep(30)"
            runner = LocalCommandRunner(
                tmp_path, {"wait": (sys.executable, "-c", script)}, Allow()
            )
            task = asyncio.create_task(runner.run("wait", "cancelled"))
            try:
                await asyncio.wait_for(started.wait(), 5)
                assert await runner.cancel("cancelled")
                with pytest.raises(asyncio.CancelledError):
                    await task
                with pytest.raises(ProcessLookupError):
                    os.kill(pids[0], 0)
                with pytest.raises(ActionError, match="new request"):
                    await runner.run("wait", "cancelled")
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_missing_executable_has_a_domain_error(tmp_path: Path) -> None:
    runner = LocalCommandRunner(
        tmp_path, {"test": ("/missing/bridge-agent-command",)}, Allow()
    )
    with pytest.raises(ActionError, match="start"):
        asyncio.run(runner.run("test", "missing"))
