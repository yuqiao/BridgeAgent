"""Skill metadata and content accessed through the public catalog."""

import asyncio
from pathlib import Path

import pytest

from bridge_agent.plugins.filesystem import LocalWorkspaceFiles
from bridge_agent.plugins.skills import FilesystemSkillCatalog


def test_reference_windows_can_continue_after_the_first_page(tmp_path: Path) -> None:
    directory = tmp_path / "skills" / "one"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        "---\nname: one\ndescription: test\n---\nRead notes.md"
    )
    (directory / "notes.md").write_text("line\n" * 200 + "last line\n")
    catalog = FilesystemSkillCatalog(LocalWorkspaceFiles(tmp_path), ("skills",))
    resource = asyncio.run(
        catalog.read_resource("one", "notes.md", start_line=201, limit=1)
    )
    assert [(line.number, line.text) for line in resource.lines] == [(201, "last line")]
    assert not resource.truncated


def test_discovery_exposes_metadata_without_skill_body(tmp_path: Path) -> None:
    directory = tmp_path / "skills" / "code-map"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        "---\nname: code-map\ndescription: Find repository entry points\n---\nSECRET_BODY_MARKER\n"
    )
    catalog = FilesystemSkillCatalog(LocalWorkspaceFiles(tmp_path), ("skills",))
    result = asyncio.run(catalog.discover())
    assert [(skill.name, skill.description) for skill in result] == [
        ("code-map", "Find repository entry points")
    ]
    assert "SECRET_BODY_MARKER" not in repr(result)


@pytest.mark.parametrize(
    "document",
    [
        "no frontmatter",
        "---\nname: valid\n---\nbody",
        "---\nname: [invalid]\ndescription: test\n---\nbody",
        "---\nname: first\nname: second\ndescription: test\n---\nbody",
    ],
)
def test_invalid_metadata_is_a_catalog_error(tmp_path: Path, document: str) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    (root / "SKILL.md").write_text(document)
    from bridge_agent.contracts.errors import SkillError

    with pytest.raises(SkillError, match="metadata"):
        asyncio.run(
            FilesystemSkillCatalog(
                LocalWorkspaceFiles(tmp_path), ("skills",)
            ).discover()
        )


def test_duplicate_skill_names_are_rejected(tmp_path: Path) -> None:
    from bridge_agent.contracts.errors import SkillError

    for name in ("one", "two"):
        directory = tmp_path / "skills" / name
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            "---\nname: duplicate\ndescription: test\n---\nbody"
        )
    with pytest.raises(SkillError, match="Duplicate"):
        asyncio.run(
            FilesystemSkillCatalog(
                LocalWorkspaceFiles(tmp_path), ("skills",)
            ).discover()
        )


def test_load_reads_only_the_named_skill_body(tmp_path: Path) -> None:
    for name in ("one", "two"):
        directory = tmp_path / "skills" / name
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: test\n---\nUse {name} instructions"
        )
    catalog = FilesystemSkillCatalog(LocalWorkspaceFiles(tmp_path), ("skills",))
    result = asyncio.run(catalog.load("one"))
    assert result.content == "Use one instructions"
    assert result.summary.name == "one"
    assert "two instructions" not in result.content


def test_skill_resources_are_read_relative_to_the_skill(tmp_path: Path) -> None:
    directory = tmp_path / "skills" / "one"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        "---\nname: one\ndescription: test\n---\nRead notes.md"
    )
    (directory / "notes.md").write_text("Use actual file citations")
    catalog = FilesystemSkillCatalog(LocalWorkspaceFiles(tmp_path), ("skills",))
    resource = asyncio.run(catalog.read_resource("one", "notes.md"))
    assert resource.lines[0].text == "Use actual file citations"


@pytest.mark.parametrize("resource", ["alias.md", "../../outside.md", ".env"])
def test_skill_resource_cannot_escape_its_directory_or_file_policy(
    tmp_path: Path, resource: str
) -> None:
    from bridge_agent.contracts.errors import SkillError

    directory = tmp_path / "skills" / "one"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text("---\nname: one\ndescription: test\n---\nbody")
    (tmp_path / "outside.md").write_text("outside skill")
    (directory / "alias.md").symlink_to(tmp_path / "outside.md")
    (directory / ".env").write_text("secret")
    catalog = FilesystemSkillCatalog(LocalWorkspaceFiles(tmp_path), ("skills",))
    with pytest.raises(SkillError):
        asyncio.run(catalog.read_resource("one", resource))


def test_discovery_rejects_a_skill_document_linked_outside_its_directory(
    tmp_path: Path,
) -> None:
    from bridge_agent.contracts.errors import SkillError

    directory = tmp_path / "skills" / "one"
    directory.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("---\nname: one\ndescription: test\n---\nbody")
    (directory / "SKILL.md").symlink_to(outside)
    with pytest.raises(SkillError):
        asyncio.run(
            FilesystemSkillCatalog(
                LocalWorkspaceFiles(tmp_path), ("skills",)
            ).discover()
        )


def test_discovery_does_not_present_a_truncated_catalog_as_complete(
    tmp_path: Path,
) -> None:
    from bridge_agent.contracts.errors import SkillError

    root = tmp_path / "skills"
    root.mkdir()
    for name in ("a", "b"):
        directory = root / name
        directory.mkdir()
        (directory / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: test\n---\nbody"
        )
    with pytest.raises(SkillError, match="incomplete"):
        asyncio.run(
            FilesystemSkillCatalog(
                LocalWorkspaceFiles(tmp_path, max_entries=1), ("skills",)
            ).discover()
        )
