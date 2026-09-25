"""Approval is a human decision; only explicit yes authorizes the displayed action."""

import asyncio
from pathlib import Path

import pytest

from bridge_agent.contracts.actions import ApprovalRequest
from bridge_agent.interfaces.approval import ConsoleApproval


@pytest.mark.parametrize(
    ("reply", "allowed"),
    [(b"y\n", True), (b"yes\n", True), (b"n\n", False), (b"\n", False), (b"", False)],
)
def test_console_requires_explicit_yes_and_displays_scope(reply, allowed, capsys):
    async def scenario():
        reader = asyncio.StreamReader()
        reader.feed_data(reply)
        reader.feed_eof()
        policy = ConsoleApproval(reader)
        assert (
            await policy.confirm(
                ApprovalRequest("write", "id", Path("/repo"), "code.py", "-old\n+new\n")
            )
            is allowed
        )

    asyncio.run(scenario())
    output = capsys.readouterr().err
    assert "/repo" in output and "code.py" in output and "-old\n+new" in output
