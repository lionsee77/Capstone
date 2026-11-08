from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from openpyxl import Workbook

from taxonomy_pipeline.providers.tag_context import TagContextProvider


@pytest.fixture
def workbook(tmp_path: Path) -> Path:
    book = Workbook()
    sheet = book.active
    sheet.title = "Unique Skills List"
    sheet.append(["skill_id", "skill_title", "skill_description", "skill_type"])
    sheet.append(["SK-B", " Loan  Processing ", "Processes loans", "Technical"])
    sheet.append(["SK-A", "Auditing", None, "Critical Core"])
    sheet.append([None, "No id", "Skipped", "Technical"])
    sheet.append(["SK-0", None, "Must not shift tag IDs", "Technical"])
    path = tmp_path / "unique.xlsx"
    book.save(path)
    return path


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
    context_path = tmp_path / "generated_tag_context.json"
    generic = TagContextProvider().load_tag_context(context_path)
    actual = {tag_id: context["title"] for tag_id, context in generic.items()}
    assert actual == expected
    assert generic["3000"] == {"skill_id": "SK-A", "title": "Auditing", "description": "", "type": "Critical Core"}
    assert generic["3001"]["description"] == "Processes loans"
    assert json.loads(context_path.read_text())["source"] == "skillsfuture"
    assert TagContextProvider().source_name(context_path) == "SkillsFuture"
    # The unmapped SK-A skill still has context, even though only SK-B has a parent link.
    assert len(generic) == 2
