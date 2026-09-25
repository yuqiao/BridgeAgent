"""Workspace behavior through the public file capability."""

import asyncio
from pathlib import Path

import pytest

from bridge_agent.contracts.errors import ConfigurationError, WorkspaceAccessError
from bridge_agent.plugins.filesystem import LocalWorkspaceFiles


def test_read_returns_a_numbered_window_from_the_workspace(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text("alpha\nbeta\ngamma\n")
    files = LocalWorkspaceFiles(tmp_path)
    result = asyncio.run(files.read("sample.py", start_line=2, limit=1))
    assert result.path == "sample.py"
    assert [(line.number, line.text) for line in result.lines] == [(2, "beta")]
    assert result.truncated


def test_invalid_ignore_file_fails_instead_of_reporting_an_empty_workspace(
    tmp_path: Path,
) -> None:
    (tmp_path / ".gitignore").write_bytes(b"\xff")
    (tmp_path / "code.py").write_text("pass")
    with pytest.raises(ConfigurationError, match="ignore file"):
        asyncio.run(LocalWorkspaceFiles(tmp_path).list())


def test_read_refuses_a_symlink_outside_the_workspace(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("private data")
    (root / "link.txt").symlink_to(outside)
    with pytest.raises(WorkspaceAccessError, match="outside workspace"):
        asyncio.run(LocalWorkspaceFiles(root).read("link.txt"))


def test_list_skips_symlink_cycles_without_abandoning_other_files(
    tmp_path: Path,
) -> None:
    (tmp_path / "loop").symlink_to("loop")
    (tmp_path / "code.py").write_text("pass")
    result = asyncio.run(LocalWorkspaceFiles(tmp_path).list())
    assert result.paths == ("code.py",)


@pytest.mark.parametrize(
    "name", [".env", ".env.local", ".git/config", "id_rsa", "key.pem"]
)
def test_sensitive_paths_cannot_be_read_through_aliases(
    tmp_path: Path, name: str
) -> None:
    target = tmp_path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("sensitive content")
    (tmp_path / "alias.txt").symlink_to(target)
    files = LocalWorkspaceFiles(tmp_path)
    for path in (name, "alias.txt"):
        with pytest.raises(WorkspaceAccessError, match="protected"):
            asyncio.run(files.read(path))


def test_list_only_returns_allowed_text_files(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("*.log\n")
    (tmp_path / "code.py").write_text("print('hello')")
    (tmp_path / "secret.log").write_text("hidden")
    (tmp_path / ".env").write_text("hidden")
    (tmp_path / "binary").write_bytes(b"\x00")
    sub = tmp_path / "src"
    sub.mkdir()
    (sub / "a.py").write_text("pass")
    result = asyncio.run(LocalWorkspaceFiles(tmp_path).list())
    assert result.paths == (".gitignore", "code.py", "src/a.py")
    assert not result.truncated


def test_search_returns_literal_matches_with_real_line_numbers(tmp_path: Path) -> None:
    (tmp_path / "code.py").write_text("header\ndef hello():\n    return 42\n")
    (tmp_path / ".env").write_text("def hidden():")
    files = LocalWorkspaceFiles(tmp_path)
    result = asyncio.run(files.search("def "))
    assert [(hit.path, hit.line, hit.text) for hit in result.hits] == [
        ("code.py", 2, "def hello():")
    ]
    assert not result.truncated


def test_traversal_budget_marks_incomplete_lists_and_searches(tmp_path: Path) -> None:
    for number in range(8):
        (tmp_path / f"{number}.txt").write_text("match")
    files = LocalWorkspaceFiles(tmp_path, max_entries=3)
    listed = asyncio.run(files.list())
    searched = asyncio.run(files.search("match"))
    assert len(listed.paths) <= 3
    assert len(searched.hits) <= 3
    assert listed.truncated and searched.truncated


def test_file_traversal_can_be_cancelled(tmp_path: Path) -> None:
    for index in range(30):
        (tmp_path / f"{index}.txt").write_text("value")

    async def scenario() -> None:
        task = asyncio.create_task(LocalWorkspaceFiles(tmp_path).search("absent"))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


@pytest.mark.parametrize("limit", [0, -1, 201, True])
def test_list_and_search_reject_invalid_limits(tmp_path: Path, limit: int) -> None:
    files = LocalWorkspaceFiles(tmp_path)
    with pytest.raises(WorkspaceAccessError, match="limit"):
        asyncio.run(files.list(limit=limit))
    with pytest.raises(WorkspaceAccessError, match="limit"):
        asyncio.run(files.search("x", limit=limit))


def test_search_output_is_bounded_for_large_matching_lines(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x" * 20000)
    result = asyncio.run(LocalWorkspaceFiles(tmp_path).search("x"))
    assert sum(len(hit.text) for hit in result.hits) <= 16000
    assert result.truncated


@pytest.mark.parametrize("query", ["", "x" * 1001])
def test_search_rejects_empty_or_excessive_queries(tmp_path: Path, query: str) -> None:
    with pytest.raises(WorkspaceAccessError, match="query"):
        asyncio.run(LocalWorkspaceFiles(tmp_path).search(query))


@pytest.mark.parametrize("path", ["", "/tmp", "../sibling"])
def test_all_operations_reject_non_relative_workspace_paths(
    tmp_path: Path, path: str
) -> None:
    files = LocalWorkspaceFiles(tmp_path)
    for operation in (files.read(path), files.list(path), files.search("x", path)):
        with pytest.raises(WorkspaceAccessError):
            asyncio.run(operation)


@pytest.mark.parametrize("start,limit", [(0, 1), (1, 0), (1, 201), (True, 1)])
def test_read_rejects_invalid_windows(tmp_path: Path, start: int, limit: int) -> None:
    (tmp_path / "a").write_text("content")
    with pytest.raises(WorkspaceAccessError, match="window"):
        asyncio.run(
            LocalWorkspaceFiles(tmp_path).read("a", start_line=start, limit=limit)
        )


@pytest.mark.parametrize("data", [b"hello\x00world", b"\xff\xfe"])
def test_read_rejects_non_text_files(tmp_path: Path, data: bytes) -> None:
    (tmp_path / "binary").write_bytes(data)
    with pytest.raises(WorkspaceAccessError, match="UTF-8 text"):
        asyncio.run(LocalWorkspaceFiles(tmp_path).read("binary"))


def test_read_rejects_non_regular_files(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceAccessError, match="regular file"):
        asyncio.run(LocalWorkspaceFiles(tmp_path).read("."))


def test_read_limits_file_size_before_loading_content(tmp_path: Path) -> None:
    (tmp_path / "large.txt").write_text("a" * (1024 * 1024 + 1))
    with pytest.raises(WorkspaceAccessError, match="size limit"):
        asyncio.run(LocalWorkspaceFiles(tmp_path).read("large.txt"))


def test_long_lines_have_bounded_output_and_an_explicit_truncation(
    tmp_path: Path,
) -> None:
    (tmp_path / "line.txt").write_text("x" * 20000)
    result = asyncio.run(LocalWorkspaceFiles(tmp_path).read("line.txt"))
    assert len(result.lines[0].text) == 16000
    assert result.truncated


def test_nested_ignore_rules_and_negation_apply_to_direct_reads(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("*.log\nblocked/\n")
    sub = tmp_path / "src"
    sub.mkdir()
    (sub / ".gitignore").write_text("!keep.log\n")
    (sub / "keep.log").write_text("allowed")
    (sub / "other.log").write_text("ignored")
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    (blocked / ".gitignore").write_text("!ok.txt\n")
    (blocked / "ok.txt").write_text("still ignored")
    files = LocalWorkspaceFiles(tmp_path)
    assert asyncio.run(files.read("src/keep.log")).lines[0].text == "allowed"
    for path in ("src/other.log", "blocked/ok.txt"):
        with pytest.raises(WorkspaceAccessError, match="ignored"):
            asyncio.run(files.read(path))
