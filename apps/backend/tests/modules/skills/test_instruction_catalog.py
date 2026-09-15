"""Bundled SKILL.md assets are constrained instructions, not graph definitions."""

from dataclasses import replace

import pytest
from pydantic import ValidationError

from industry_platform.modules.skills.registry import InstructionSkillCatalog, load_bundled_skills
from industry_platform.modules.skills.tool import SkillReadInput


def test_catalog_loads_packaged_documents_with_stable_exact_content_identity() -> None:
    catalog = load_bundled_skills()
    assert catalog == load_bundled_skills()
    assert {item.name for item in catalog.skills} == {
        "industry-news-brief",
        "financial-excerpt-explanation",
        "financial-basis-comparison",
    }
    for skill in catalog.skills:
        assert catalog.resolve(skill.name, skill.content_sha256) == skill
        assert len(skill.content_sha256) == 64
        assert skill.instructions.startswith("# ")
        assert skill.instructions not in catalog.context(())
    with pytest.raises(ValueError, match="Duplicate"):
        InstructionSkillCatalog((catalog.skills[0], catalog.skills[0]))
    with pytest.raises(ValueError, match="Unknown"):
        catalog.resolve("nonexistent", catalog.skills[0].content_sha256)
    changed = replace(catalog.skills[0], content_sha256="0" * 64)
    with pytest.raises(ValueError, match="changed"):
        catalog.resolve(changed.name, changed.content_sha256)


@pytest.mark.parametrize("name", ["../secrets", "D:/private", "/etc/passwd", "skill.md", "a/b", ""])
def test_reader_never_accepts_a_filesystem_path(name: str) -> None:
    with pytest.raises(ValidationError):
        SkillReadInput(name=name, content_sha256="0" * 64)


def test_reader_rejects_executable_or_permission_arguments() -> None:
    with pytest.raises(ValidationError):
        SkillReadInput.model_validate(
            {
                "name": "industry-news-brief",
                "content_sha256": "0" * 64,
                "command": "run",
                "allowed_tools": ["finance.calculate"],
            }
        )
