from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from openpyxl import Workbook

from taxonomy_pipeline.providers.skillsfuture import (
    SHEET,
    TAG_ID_OFFSET,
    UNIQUE_SKILLS_WORKBOOK,
    SkillsFutureProvider,
    build_skill_tag_mapping,
    source_skills,
)


@pytest.fixture
def workbook(tmp_path: Path) -> Path:
    book = Workbook()
    sheet = book.active
    sheet.title = SHEET
    sheet.append(["skill_id", "skill_title", "skill_description", "skill_type"])
    sheet.append(["SK-B", " Loan  Processing ", "Processes loans", "Technical"])
    sheet.append(["SK-A", "Auditing", None, "Critical Core"])
    sheet.append([None, "No id", "Skipped", "Technical"])
    sheet.append(["SK-0", None, "Must not shift tag IDs", "Technical"])
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


def test_mapping_sorts_ids_and_excludes_untitled_or_missing_ids() -> None:
    assert build_skill_tag_mapping({
        "SK-B": "Second skill", "SK-A": "First skill", "SK-0": "  ", "": "Missing ID",
    }) == {"SK-A": 3000, "SK-B": 3001}


def test_mapping_normalises_workbook_whitespace() -> None:
    assert build_skill_tag_mapping({" SK- A ": "A title", "SK-B": "Another"}) == {
        "SK- A": 3000, "SK-B": 3001,
    }


def test_empty_workbook_has_no_mapping() -> None:
    assert build_skill_tag_mapping({}) == {}


def test_generator_reader_and_provider_assign_matching_ids_and_titles(
    workbook: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    from taxonomy_pipeline.taxonomy.sql_dump import iter_insert_statements

    generator_path = Path(__file__).parents[2] / "skillsfuture" / "generate_skillsfuture_taxonomy.py"
    spec = importlib.util.spec_from_file_location("standalone_generator", generator_path)
    assert spec is not None and spec.loader is not None
    generator = importlib.util.module_from_spec(spec)
    import builtins

    original_import = builtins.__import__

    def standalone_import(name: str, *args, **kwargs):
        if name == "taxonomy_pipeline" or name.startswith("taxonomy_pipeline."):
            raise ModuleNotFoundError("Standalone generator must not need taxonomy_pipeline")
        return original_import(name, *args, **kwargs)

    with monkeypatch.context() as isolated:
        isolated.setattr(builtins, "__import__", standalone_import)
        spec.loader.exec_module(generator)
    framework = tmp_path / "framework.xlsx"
    mapping = tmp_path / "mapping.xlsx"
    for path, sheet_name, headers, values in [
        (framework, "Job Role_TSC_CCS", ["Sector", "Job Role", "TSC_CCS Title", "TSC_CCS Code"],
         ["Finance", "Analyst", "Loans", "TSC-1"]),
        (mapping, "data", ["skills_framework_skill_code", "Unique skill_updated_skill_title"],
         ["TSC-1", "Loan Processing"]),
    ]:
        book = Workbook()
        sheet = book.active
        sheet.title = sheet_name
        sheet.append(headers)
        sheet.append(values)
        book.save(path)
        book.close()
    output = tmp_path / "generated.sql"
    monkeypatch.setattr(sys, "argv", [
        str(generator_path), "--unique", str(workbook), "--framework", str(framework),
        "--mapping", str(mapping), "--output", str(output),
    ])
    generator.main()
    expected = {
        str(row["id"]): row["name"]
        for table, rows in iter_insert_statements(output.read_text()) if table == "sf_tags" for row in rows
    }
    actual = {tag_id: context["title"] for tag_id, context in SkillsFutureProvider().load_tag_context(workbook).items()}
    assert actual == expected
