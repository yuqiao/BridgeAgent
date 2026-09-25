"""Reviewable local edits using the workspace file policy."""

import asyncio
import os
import tempfile
from difflib import unified_diff
from pathlib import Path
from uuid import uuid4

from bridge_agent.contracts.actions import (
    ApprovalPolicy,
    ApprovalRequest,
    ChangePreview,
    ChangeResult,
)
from bridge_agent.contracts.errors import ActionError
from bridge_agent.plugins.file_policy import FilePolicy


class LocalWorkspaceChanges:
    def __init__(self, root: Path, approval: ApprovalPolicy) -> None:
        self._files = FilePolicy(root)
        self._approval = approval
        self._pending: dict[str, tuple[ChangePreview, Path, str, str | None]] = {}
        self._applied: dict[str, ChangeResult] = {}
        self._lock = asyncio.Lock()

    async def preview(self, path: str, content: str) -> ChangePreview:
        if len(content) > 16000 or "\x00" in content:
            raise ActionError("Change content must be bounded text")
        target = self._files.target(path)
        before = self._files.text(target) if target.exists() else ""
        if len(before) > 16000:
            raise ActionError("Original file exceeds change preview limit")
        diff = "".join(
            unified_diff(
                before.splitlines(keepends=True),
                content.splitlines(keepends=True),
                fromfile=path,
                tofile=path,
            )
        )
        preview = ChangePreview(
            str(uuid4()), target.relative_to(self._files.root).as_posix(), diff
        )
        self._pending[preview.change_id] = (
            preview,
            target,
            content,
            before if target.exists() else None,
        )
        return preview

    async def apply(self, change_id: str) -> ChangeResult:
        async with self._lock:
            return await self._apply(change_id)

    async def _apply(self, change_id: str) -> ChangeResult:
        if change_id in self._applied:
            result = self._applied[change_id]
            return ChangeResult(change_id, result.path, "already_applied")
        if change_id not in self._pending:
            raise ActionError("Unknown or expired change; create a new preview")
        preview, target, content, before = self._pending[change_id]
        if not await self._approval.confirm(
            ApprovalRequest(
                "write", change_id, self._files.root, preview.path, preview.diff
            )
        ):
            raise ActionError("File change denied")
        current = self._files.target(preview.path)
        actual = self._files.text(current) if current.exists() else None
        if current != target or actual != before:
            raise ActionError("File changed since preview; create a new preview")
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
                temporary = Path(stream.name)
                if target.exists():
                    os.fchmod(stream.fileno(), target.stat().st_mode & 0o777)
                stream.write(content.encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        except OSError:
            raise ActionError("Cannot apply file change") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        result = ChangeResult(change_id, preview.path, "applied")
        self._applied[change_id] = result
        del self._pending[change_id]
        return result
