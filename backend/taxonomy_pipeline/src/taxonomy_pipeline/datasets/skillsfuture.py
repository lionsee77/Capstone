"""Shared workbook identity rules keep generated tags and classifier context aligned."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def normalise_skill_text(value: Any) -> str:
    """Use the generator's whitespace rules for workbook IDs and titles."""
    return " ".join(str(value or "").split())


def build_skill_tag_mapping(skills: Mapping[str, str]) -> dict[str, int]:
    """Assign IDs only to titled skills, in the generator's stable sorted order.

    Both SQL generation and Case B must call this function with workbook IDs
    mapped to titles; an untitled workbook row must never shift subsequent IDs.
    """
    eligible = {
        normalise_skill_text(skill_id)
        for skill_id, title in skills.items()
        if normalise_skill_text(skill_id) and normalise_skill_text(title)
    }
    return {skill_id: 3000 + index for index, skill_id in enumerate(sorted(eligible))}
