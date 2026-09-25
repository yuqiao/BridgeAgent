"""File changes are previewed, approved, and applied through one public capability."""

import asyncio
from pathlib import Path

import pytest

from bridge_agent.plugins.changes import LocalWorkspaceChanges


class Allow:
    async def confirm(self, request):
        return True


@pytest.mark.parametrize(
    "content", ["x" * 16001, "bad\x00text"], ids=["too-large", "binary"]
)
def test_unreviewable_content_is_rejected_before_approval(
    tmp_path: Path, content: str
) -> None:
    from bridge_agent.contracts.errors import ActionError

    changes = LocalWorkspaceChanges(tmp_path, Allow())
    with pytest.raises(ActionError, match="content"):
        asyncio.run(changes.preview("new.txt", content))


def test_unknown_or_expired_preview_requires_a_new_preview(tmp_path: Path) -> None:
    from bridge_agent.contracts.errors import ActionError

    with pytest.raises(ActionError, match="preview"):
        asyncio.run(LocalWorkspaceChanges(tmp_path, Allow()).apply("missing"))


def test_simultaneous_submission_of_one_change_is_applied_once(tmp_path: Path) -> None:
    async def scenario() -> None:
        entered, release = asyncio.Event(), asyncio.Event()

        class Approver:
            async def confirm(self, request):
                entered.set()
                await release.wait()
                return True

        (tmp_path / "file").write_text("old")
        changes = LocalWorkspaceChanges(tmp_path, Approver())
        preview = await changes.preview("file", "new")
        first = asyncio.create_task(changes.apply(preview.change_id))
        await entered.wait()
        second = asyncio.create_task(changes.apply(preview.change_id))
        release.set()
        results = await asyncio.gather(first, second)
        assert {result.status for result in results} == {"applied", "already_applied"}

    asyncio.run(scenario())


def test_preview_reports_a_diff_without_changing_the_file(tmp_path: Path) -> None:
    path = tmp_path / "code.py"
    path.write_text("answer = 41\n")
    changes = LocalWorkspaceChanges(tmp_path, Allow())
    preview = asyncio.run(changes.preview("code.py", "answer = 42\n"))
    assert "-answer = 41" in preview.diff and "+answer = 42" in preview.diff
    assert preview.path == "code.py"
    assert path.read_text() == "answer = 41\n"


def test_approved_change_is_applied_and_duplicate_submission_is_not_replayed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "code.py"
    path.write_text("answer = 41\n")
    changes = LocalWorkspaceChanges(tmp_path, Allow())

    async def scenario() -> None:
        preview = await changes.preview("code.py", "answer = 42\n")
        result = await changes.apply(preview.change_id)
        assert result.status == "applied"
        assert path.read_text() == "answer = 42\n"
        path.write_text("external change\n")
        repeated = await changes.apply(preview.change_id)
        assert repeated.status == "already_applied"
        assert path.read_text() == "external change\n"

    asyncio.run(scenario())


def test_interrupted_write_does_not_partially_replace_the_file(
    tmp_path: Path, monkeypatch
) -> None:
    import os

    from bridge_agent.contracts.errors import ActionError

    path = tmp_path / "code.py"
    path.write_text("old\n")
    changes = LocalWorkspaceChanges(tmp_path, Allow())

    def refuse_replace(source, destination):
        raise OSError("device unavailable")

    monkeypatch.setattr(os, "replace", refuse_replace)

    async def scenario() -> None:
        preview = await changes.preview("code.py", "new\n")
        with pytest.raises(ActionError, match="apply"):
            await changes.apply(preview.change_id)
        assert path.read_text() == "old\n"
        assert {item.name for item in tmp_path.iterdir()} == {"code.py"}

    asyncio.run(scenario())


def test_denied_change_leaves_the_file_unchanged(tmp_path: Path) -> None:
    from bridge_agent.contracts.errors import ActionError

    requests = []

    class Deny:
        async def confirm(self, request):
            requests.append(request)
            return False

    path = tmp_path / "code.py"
    path.write_text("old\n")
    changes = LocalWorkspaceChanges(tmp_path, Deny())

    async def scenario() -> None:
        preview = await changes.preview("code.py", "new\n")
        with pytest.raises(ActionError, match="denied"):
            await changes.apply(preview.change_id)
        assert requests[0].details == preview.diff
        assert requests[0].workspace == tmp_path.resolve()
        assert path.read_text() == "old\n"

    asyncio.run(scenario())


@pytest.mark.parametrize("during_approval", [False, True])
def test_stale_preview_does_not_overwrite_external_changes(
    tmp_path: Path, during_approval: bool
) -> None:
    from bridge_agent.contracts.errors import ActionError

    path = tmp_path / "code.py"
    path.write_text("old\n")

    class Approver:
        async def confirm(self, request):
            if during_approval:
                path.write_text("external\n")
            return True

    changes = LocalWorkspaceChanges(tmp_path, Approver())

    async def scenario() -> None:
        preview = await changes.preview("code.py", "new\n")
        if not during_approval:
            path.write_text("external\n")
        with pytest.raises(ActionError, match="changed"):
            await changes.apply(preview.change_id)
        assert path.read_text() == "external\n"

    asyncio.run(scenario())


def test_preview_refuses_a_huge_original_diff(tmp_path: Path) -> None:
    from bridge_agent.contracts.errors import ActionError

    (tmp_path / "large.txt").write_text("x" * 20000)
    changes = LocalWorkspaceChanges(tmp_path, Allow())
    with pytest.raises(ActionError, match="limit"):
        asyncio.run(changes.preview("large.txt", "small"))


@pytest.mark.parametrize(
    "path",
    ["../outside", ".env", ".git/config", "private.key", "ignored.txt", "alias.txt"],
)
def test_write_preview_uses_the_read_boundary(tmp_path, path):
    from bridge_agent.contracts.errors import WorkspaceAccessError

    (tmp_path / ".gitignore").write_text("ignored.txt\n")
    (tmp_path / "alias.txt").symlink_to(tmp_path.parent / "outside")
    with pytest.raises(WorkspaceAccessError):
        asyncio.run(LocalWorkspaceChanges(tmp_path, Allow()).preview(path, "new"))


def test_new_file_can_be_previewed_and_created(tmp_path):
    async def scenario():
        changes = LocalWorkspaceChanges(tmp_path, Allow())
        preview = await changes.preview("new.txt", "new\n")
        assert not (tmp_path / "new.txt").exists()
        await changes.apply(preview.change_id)
        assert (tmp_path / "new.txt").read_text() == "new\n"

    asyncio.run(scenario())
