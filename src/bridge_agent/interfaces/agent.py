"""Run a configured Agent in one workspace."""

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

from bridge_agent.application.agent import AgentSession
from bridge_agent.bootstrap.agent import agent_catalog, read_environment
from bridge_agent.contracts.agent import AGENT_RUNTIME, RunResult
from bridge_agent.contracts.errors import AgentSessionError, BridgeAgentError
from bridge_agent.contracts.sessions import SESSION_CONTROL
from bridge_agent.interfaces.approval import ConsoleApproval
from bridge_agent.kernel.host import PluginHost


def _print_result(result: RunResult, show_tools: bool) -> None:
    if show_tools:
        for item in result.tools:
            print(f"{item.name}: {item.content}", file=sys.stderr)
    print(result.text, flush=True)


async def run(arguments: argparse.Namespace) -> int:
    reader = asyncio.StreamReader()
    interactive = sys.stdin.isatty()
    transport = None
    if interactive or (arguments.prompt is None and not arguments.session_status):
        transport, _ = await asyncio.get_running_loop().connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), sys.stdin
        )
    try:
        environment = read_environment(
            arguments.env_file, override=arguments.env_override
        )
        catalog = agent_catalog(
            environment=environment,
            workspace=arguments.workspace,
            approval=ConsoleApproval(reader) if interactive else None,
        )
        async with PluginHost(catalog.load(arguments.config)) as host:
            if arguments.session_status:
                info = await host.resolve(SESSION_CONTROL).get_session(
                    arguments.session_id
                )
                if info is None:
                    raise AgentSessionError("Session not found")
                print(json.dumps(asdict(info), default=str))
                return 0
            session = AgentSession(
                host.resolve(AGENT_RUNTIME),
                arguments.workspace,
                session_id=arguments.session_id,
            )
            if arguments.prompt is not None:
                _print_result(await session.ask(arguments.prompt), arguments.show_tools)
                return 0
            if interactive:
                print(f"Session: {session.session_id}", file=sys.stderr)
            status = 0
            while True:
                if interactive:
                    print("> ", end="", file=sys.stderr, flush=True)
                line = await reader.readline()
                if not line:
                    break
                text = line.decode("utf-8").strip()
                if text == "/exit":
                    break
                if text == "/new":
                    session = AgentSession(
                        host.resolve(AGENT_RUNTIME), arguments.workspace
                    )
                    print(f"New session: {session.session_id}", file=sys.stderr)
                elif text:
                    try:
                        _print_result(await session.ask(text), arguments.show_tools)
                    except BridgeAgentError as error:
                        print(str(error), file=sys.stderr)
                        status = 1
            return status
    finally:
        if transport is not None:
            transport.close()


def _error_lines(error: BaseException) -> list[str]:
    if isinstance(error, BaseExceptionGroup):
        return [line for child in error.exceptions for line in _error_lines(child)]
    if isinstance(error, BridgeAgentError):
        lines = [str(error)]
        if isinstance(error.__cause__, BridgeAgentError):
            lines.extend(_error_lines(error.__cause__))
        return lines
    return ["Agent failed; inspect the chained error through the Python interface"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument(
        "--env-override",
        action="store_true",
        help="Prefer explicitly supplied env-file values over process environment",
    )
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument(
        "--prompt", help="Single task; omit for serial chat (/new, /exit)"
    )
    parser.add_argument("--show-tools", action="store_true")
    parser.add_argument(
        "--session-id",
        help="Continue or create this session in the configured checkpoint store",
    )
    parser.add_argument(
        "--session-status",
        action="store_true",
        help="Inspect a saved session without calling the model",
    )
    arguments = parser.parse_args()
    if arguments.session_status and (
        not arguments.session_id or arguments.prompt is not None
    ):
        parser.error(
            "--session-status requires --session-id and cannot be used with --prompt"
        )
    try:
        return asyncio.run(run(arguments))
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130
    except Exception as error:
        for line in _error_lines(error):
            print(line, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
