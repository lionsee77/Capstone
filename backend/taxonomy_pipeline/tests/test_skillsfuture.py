from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook

from taxonomy_pipeline.providers.skillsfuture import (
    SHEET,
    TAG_ID_OFFSET,
    UNIQUE_SKILLS_WORKBOOK,
    SkillsFutureProvider,
    source_skills,
)


@pytest.fixture
def workbook(tmp_path: Path) -> Path:
    book = Workbook()
    sheet = book.active
    sheet.title = SHEET
    sheet.append(["skill_id", "skill_title", "skill_description", "skill_type"])
    sheet.append(["SK-B", " Loan Processing ", "Processes loans", "Technical"])
    sheet.append(["SK-A", "Auditing", None, "Critical Core"])
    sheet.append([None, "No id", "Skipped", "Technical"])
    path = tmp_path / "unique.xlsx"
    book.save(path)
    return path


def test_reads_each_skills_title_description_and_type_by_skill_id(workbook: Path) -> None:
    assert source_skills(workbook) == {
        "SK-B": {"title": "Loan Processing", "description": "Processes loans", "type": "Technical"},
        "SK-A": {"title": "Auditing", "description": "", "type": "Critical Core"},
    }


def test_numbers_tags_from_the_offset_in_sorted_skill_id_order(workbook: Path) -> None:
    contexts = SkillsFutureProvider().load_tag_context(workbook)
    assert list(contexts) == [str(TAG_ID_OFFSET), str(TAG_ID_OFFSET + 1)]
    assert contexts[str(TAG_ID_OFFSET)] == {
        "skill_id": "SK-A", "title": "Auditing", "description": "", "type": "Critical Core",
    }
    assert contexts[str(TAG_ID_OFFSET + 1)]["skill_id"] == "SK-B"


def test_a_tag_with_no_skill_gets_a_null_skill_id(workbook: Path) -> None:
    provider = SkillsFutureProvider()
    assert provider.context_for(provider.load_tag_context(workbook), "9999") == {"skill_id": None}


def test_defaults_to_the_committed_unique_skills_workbook() -> None:
    provider = SkillsFutureProvider()
    assert provider.default_context_path() == UNIQUE_SKILLS_WORKBOOK
    assert UNIQUE_SKILLS_WORKBOOK.parent.name == "skillsfuture"
