from __future__ import annotations

import pytest
from openpyxl import Workbook

from taxonomy_pipeline.datasets.ssic import HEADERS, SHEET, SsicSource


def write_workbook(tmp_path, rows, sheet=SHEET, headers=HEADERS):
    path = tmp_path / "ssic.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = sheet
    worksheet.append(["Classification structure"])
    worksheet.append([])
    worksheet.append(list(headers))
    for row in rows:
        worksheet.append(row)
    workbook.save(path)
    workbook.close()
    return path


def test_load_hierarchy_and_select_level(tmp_path):
    path = write_workbook(tmp_path, [
        ("A", "Agriculture"),
        ("01", "Agriculture activities"),
        (None, None),
        ("011", "Growing crops"),
        ("0111", "Food crops"),
        ("01111", "Growing vegetables"),
        ("B", "Mining"),
        ("05", "Coal mining"),
    ])
    reference = SsicSource(path).load()
    assert reference.system == "SSIC"
    assert reference.version == "2025"
    assert reference.source_path == path.resolve()
    assert [(r.code, r.parent_code) for r in reference.records] == [
        ("A", None), ("01", "A"), ("011", "01"), ("0111", "011"),
        ("01111", "0111"), ("B", None), ("05", "B"),
    ]
    assert [r.level for r in reference.records[:5]] == [
        "section", "division", "group", "class", "subclass"
    ]
    assert [r.code for r in reference.at_level("division")] == ["01", "05"]
    assert reference.records[4].title == "Growing vegetables"
    assert len(reference.records) == 7
    with pytest.raises(ValueError, match="Unsupported SSIC level"):
        reference.at_level("occupation")


@pytest.mark.parametrize("rows, message", [
    ([], "no SSIC classification records"),
    ([("A", "Agriculture"), ("A", "Again")], "row 5: duplicate"),
    ([("A", None)], "row 4: missing title"),
    ([("?", "Invalid")], "row 4: invalid SSIC code"),
    ([(None, "Missing code")], "row 4: code must be text"),
    ([(1, "Numeric code")], "row 4: code must be text"),
    ([("01", "No section")], "row 4: missing parent"),
    ([("A", "Agriculture"), ("011", "No division")], "row 5: missing parent"),
])
def test_invalid_records(tmp_path, rows, message):
    with pytest.raises(ValueError, match=message):
        SsicSource(write_workbook(tmp_path, rows)).load()


def test_missing_sheet(tmp_path):
    with pytest.raises(ValueError, match="missing sheet"):
        SsicSource(write_workbook(tmp_path, [], sheet="Wrong sheet")).load()


def test_missing_headers(tmp_path):
    with pytest.raises(ValueError, match="missing headers"):
        SsicSource(write_workbook(tmp_path, [], headers=("Code", "Title"))).load()


def test_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        SsicSource(tmp_path / "missing.xlsx").load()
