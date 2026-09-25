"""Internal path and text policy shared by local readers and writers."""

from pathlib import Path

from pathspec import GitIgnoreSpec

from bridge_agent.contracts.errors import ConfigurationError, WorkspaceAccessError


class FilePolicy:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        if not self.root.is_dir():
            raise WorkspaceAccessError("Workspace must be an existing directory")

    def _ignored(self, relative: Path) -> bool:
        rules: list[tuple[Path, GitIgnoreSpec]] = []
        parent = self.root
        for part in relative.parts:
            ignore = parent / ".gitignore"
            if ignore.is_file():
                if ignore.is_symlink() or ignore.stat().st_size > 65536:
                    raise ConfigurationError("Invalid ignore file")
                try:
                    with ignore.open("rb") as stream:
                        data = stream.read(65537)
                    if len(data) > 65536:
                        raise ConfigurationError("Invalid ignore file")
                    spec = GitIgnoreSpec.from_lines(data.decode("utf-8").splitlines())
                except (OSError, UnicodeError, ValueError):
                    raise ConfigurationError("Invalid ignore file") from None
                rules.append((parent, spec))
            candidate = parent / part
            ignored = False
            for base, spec in rules:
                name = candidate.relative_to(base).as_posix()
                if candidate.is_dir():
                    name += "/"
                match = spec.check_file(name)
                if match.include is not None:
                    ignored = match.include
            if ignored:
                return True
            parent = candidate
        return False

    def target(self, path: str) -> Path:
        if (
            not path
            or Path(path).is_absolute()
            or ".." in Path(path).parts
            or "\x00" in path
        ):
            raise WorkspaceAccessError("Expected a relative workspace path")
        target = (self.root / path).resolve()
        if not target.is_relative_to(self.root):
            raise WorkspaceAccessError("Path is outside workspace")
        for candidate in (Path(path), target.relative_to(self.root)):
            if any(
                part.lower()
                in {".git", ".ssh", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}
                or part.lower().startswith(".env")
                or part.lower().endswith((".pem", ".key", ".p12", ".pfx"))
                for part in candidate.parts
            ):
                raise WorkspaceAccessError("Path is protected")
            if self._ignored(candidate):
                raise WorkspaceAccessError("Path is ignored")
        return target

    def text(self, target: Path) -> str:
        if not target.is_file():
            raise WorkspaceAccessError("Path must be an existing regular file")
        if target.stat().st_size > 1024 * 1024:
            raise WorkspaceAccessError("File exceeds size limit")
        try:
            with target.open("rb") as stream:
                data = stream.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                raise WorkspaceAccessError("File exceeds size limit")
            text = data.decode("utf-8")
        except UnicodeError:
            raise WorkspaceAccessError("File must contain UTF-8 text") from None
        if "\x00" in text:
            raise WorkspaceAccessError("File must contain UTF-8 text")
        return text
