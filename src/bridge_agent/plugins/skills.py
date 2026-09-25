"""Skill catalog over the same file policy used by repository tools."""

from pathlib import PurePosixPath

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from yaml.nodes import MappingNode, ScalarNode
from yaml.tokens import AliasToken, AnchorToken

from bridge_agent.contracts.errors import SkillError, WorkspaceAccessError
from bridge_agent.contracts.files import FileRead, WorkspaceFiles
from bridge_agent.contracts.skills import LoadedSkill, SkillSummary


class SkillMetadata(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)
    description: str = Field(min_length=1, max_length=500)


def parse_skill(text: str, path: str) -> tuple[SkillSummary, str]:
    try:
        if not text.startswith("---\n"):
            raise ValueError
        header, separator, body = text[4:].partition("\n---\n")
        if not separator or len(header) > 4096:
            raise ValueError
        if any(
            isinstance(token, (AliasToken, AnchorToken)) for token in yaml.scan(header)
        ):
            raise ValueError
        node = yaml.compose(header, Loader=yaml.SafeLoader)
        if not isinstance(node, MappingNode):
            raise ValueError
        data = {}
        for key, value in node.value:
            if (
                not isinstance(key, ScalarNode)
                or not isinstance(value, ScalarNode)
                or value.tag != "tag:yaml.org,2002:str"
                or key.value in data
            ):
                raise ValueError
            data[key.value] = value.value
        metadata = SkillMetadata.model_validate(data)
    except (yaml.YAMLError, ValueError, ValidationError, RecursionError):
        raise SkillError("Invalid skill metadata") from None
    return SkillSummary(metadata.name, metadata.description, path), body


class FilesystemSkillCatalog:
    def __init__(self, files: WorkspaceFiles, roots: tuple[str, ...]) -> None:
        self._files = files
        self._roots = roots

    async def discover(self) -> tuple[SkillSummary, ...]:
        summaries: list[SkillSummary] = []
        for root in self._roots:
            listing = await self._files.list(root)
            if listing.truncated:
                raise SkillError(
                    "Skill catalog is incomplete; narrow the roots or increase traversal budget"
                )
            for path in listing.paths:
                if PurePosixPath(path).name != "SKILL.md":
                    continue
                try:
                    result = await self._files.read(
                        path, scope=PurePosixPath(path).parent.as_posix()
                    )
                except WorkspaceAccessError:
                    raise SkillError(
                        "Skill document is unavailable or denied"
                    ) from None
                text = "\n".join(line.text for line in result.lines)
                summary, _ = parse_skill(text, path)
                if any(item.name == summary.name for item in summaries):
                    raise SkillError("Duplicate skill name")
                summaries.append(summary)
        return tuple(sorted(summaries, key=lambda item: item.name))

    async def load(self, name: str) -> LoadedSkill:
        summary = next(
            (item for item in await self.discover() if item.name == name), None
        )
        if summary is None:
            raise SkillError("Unknown skill")
        try:
            result = await self._files.read(
                summary.path, scope=PurePosixPath(summary.path).parent.as_posix()
            )
        except WorkspaceAccessError:
            raise SkillError("Skill document is unavailable or denied") from None
        if result.truncated:
            raise SkillError("Skill body exceeds the loading limit")
        current, body = parse_skill(
            "\n".join(line.text for line in result.lines), summary.path
        )
        if current != summary:
            raise SkillError("Skill changed during loading; retry discovery")
        return LoadedSkill(summary, body)

    async def read_resource(
        self, name: str, path: str, *, start_line: int = 1, limit: int = 200
    ) -> FileRead:
        skill = await self.load(name)
        parent = PurePosixPath(skill.summary.path).parent
        if (
            PurePosixPath(path).is_absolute()
            or ".." in PurePosixPath(path).parts
            or not path
        ):
            raise SkillError("Invalid skill resource path")
        resource = parent / path
        try:
            return await self._files.read(
                resource.as_posix(),
                scope=parent.as_posix(),
                start_line=start_line,
                limit=limit,
            )
        except WorkspaceAccessError:
            raise SkillError("Skill resource is unavailable or denied") from None
