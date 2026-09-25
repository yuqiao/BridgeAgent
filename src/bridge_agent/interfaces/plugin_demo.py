"""Run the phase-1 composition demo; this is not an Agent chat interface."""

import argparse
import asyncio
import sys
from pathlib import Path

from bridge_agent.application.demo import render_report
from bridge_agent.bootstrap.demo import demo_catalog
from bridge_agent.contracts.demo import REPORT
from bridge_agent.contracts.errors import BridgeAgentError
from bridge_agent.kernel.host import PluginHost


async def run(config: Path, text: str) -> str:
    async with PluginHost(demo_catalog().load(config)) as host:
        return render_report(host.resolve(REPORT), text)


def _error_lines(error: BaseException) -> list[str]:
    if isinstance(error, BaseExceptionGroup):
        return [line for child in error.exceptions for line in _error_lines(child)]
    if isinstance(error, BridgeAgentError):
        return [str(error)]
    return [
        f"Plugin error ({type(error).__name__}); inspect the chained error in Python"
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--text", required=True)
    arguments = parser.parse_args()
    try:
        result = asyncio.run(run(arguments.config, arguments.text))
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130
    except Exception as error:
        for line in _error_lines(error):
            print(line, file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
