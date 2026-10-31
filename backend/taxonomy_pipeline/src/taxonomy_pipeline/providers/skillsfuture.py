"""SkillsFuture: per-tag skill context for Case B, from the unique-skills workbook.

- `TAG_ID_OFFSET`: the generated SkillsFuture taxonomy numbers its tags from 3000,
  in sorted skill-ID order, so tag `3000 + n` is the n-th unique skill.
- `UNIQUE_SKILLS_WORKBOOK`: the unique-skills list each skill's title,
  description and type are read from, for prompt context.

Nothing outside `providers/` knows either; Case B asks `SkillsFutureProvider`.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

from .base import SourceProvider

TAG_ID_OFFSET = 3000


def normalise_skill_text(value: Any) -> str:
    """Apply the SQL generator's whitespace rules to workbook identifiers and titles."""
    return " ".join(str(value or "").split())


def build_skill_tag_mapping(skills: Mapping[str, str]) -> dict[str, int]:
    """Mirror standalone generation; cross-reader parity is enforced by tests."""
    eligible = {
        normalise_skill_text(skill_id)
        for skill_id, title in skills.items()
        if normalise_skill_text(skill_id) and normalise_skill_text(title)
    }
    return {skill_id: TAG_ID_OFFSET + index for index, skill_id in enumerate(sorted(eligible))}

# backend/skillsfuture/ when installed in editable mode (src/taxonomy_pipeline/providers/ -> backend/).
UNIQUE_SKILLS_WORKBOOK = (
    Path(__file__).resolve().parents[4] / "skillsfuture" / "jobsandskills-skillsfuture-unique-skills-list.xlsx"
)
SHEET = "Unique Skills List"


def source_skills(path: str | Path) -> dict[str, dict[str, str]]:
    """Skill title, description and type by SkillsFuture skill_id."""
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        rows = workbook[SHEET].iter_rows(values_only=True)
        headers = [str(value or "").strip() for value in next(rows)]
        result = {}
        for values in rows:
            row = dict(zip(headers, values, strict=False))
            skill_id = normalise_skill_text(row.get("skill_id"))
            if skill_id and normalise_skill_text(row.get("skill_title")):
                result[skill_id] = {
                    field: normalise_skill_text(row.get(f"skill_{field}")) for field in ("title", "description", "type")
                }
        return result
    finally:
        workbook.close()


class SkillsFutureProvider(SourceProvider):
    """Maps each generated tag ID back to its skill and adds the skill's workbook details."""

    name: ClassVar[str] = "skillsfuture"
    display_name: ClassVar[str] = "SkillsFuture"
    missing_context: ClassVar[dict[str, Any]] = {"skill_id": None}

    def default_context_path(self) -> Path:
        return UNIQUE_SKILLS_WORKBOOK

    def load_tag_context(self, path: str | Path | None) -> dict[str, dict[str, Any]]:
        skills = source_skills(path or self.default_context_path())
        return {
            str(tag_id): {"skill_id": skill_id, **skills[skill_id]}
            for skill_id, tag_id in build_skill_tag_mapping({key: row["title"] for key, row in skills.items()}).items()
        }
