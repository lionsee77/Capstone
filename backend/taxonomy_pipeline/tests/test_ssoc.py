from __future__ import annotations

import pytest
from openpyxl import Workbook

from taxonomy_pipeline.datasets.ssoc import HEADERS, SHEET, SsocSource


def write_workbook(tmp_path, rows, sheet=SHEET, headers=HEADERS):
    path = tmp_path / "ssoc.xlsx"
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
        ("1", "Managers"),
        ("11", "Senior officials"),
        (None, None),
        ("111", "Legislators and senior officials"),
        ("1111", "Legislators"),
        ("11110", "Legislator"),
        ("2", "Professionals"),
        ("21", "Science and engineering professionals"),
    ])
    reference = SsocSource(path).load()
    assert reference.system == "SSOC"
    assert reference.version == "2024"
    assert reference.source_path == path.resolve()
    assert [(r.code, r.parent_code) for r in reference.records] == [
        ("1", None), ("11", "1"), ("111", "11"), ("1111", "111"),
        ("11110", "1111"), ("2", None), ("21", "2"),
    ]
    assert [r.level for r in reference.records[:5]] == [
        "major_group", "sub_major_group", "minor_group", "unit_group", "occupation",
    ]
    assert [r.code for r in reference.at_level("minor_group")] == ["111"]
    assert reference.records[4].title == "Legislator"
    assert len(reference.records) == 7
    with pytest.raises(ValueError, match="Unsupported SSOC level"):
        reference.at_level("division")


def test_special_occupations_belong_directly_to_x(tmp_path):
    rows = [("X", "Workers not elsewhere classified")]
    rows.extend((f"X{i}000", f"Special occupation {i}") for i in range(1, 6))
    reference = SsocSource(write_workbook(tmp_path, rows)).load()
    assert reference.records[0].parent_code is None
    assert reference.records[0].level == "major_group"
    assert [(r.code, r.parent_code) for r in reference.at_level("occupation")] == [
        (f"X{i}000", "X") for i in range(1, 6)
    ]
    assert reference.at_level("unit_group") == []


@pytest.mark.parametrize("rows, message", [
    ([], "no SSOC classification records"),
    ([("1", "Managers"), ("1", "Again")], "row 5: duplicate"),
    ([("1", None)], "row 4: missing title"),
    ([(None, "Missing code")], "row 4: code must be text"),
    ([(1, "Numeric code")], "row 4: code must be text"),
    ([("11", "No major group")], "row 4: missing parent"),
    ([("1", "Managers"), ("111", "No sub-major group")], "row 5: missing parent"),
    ([("X1000", "No X parent")], "row 4: missing parent"),
])
def test_invalid_records(tmp_path, rows, message):
    with pytest.raises(ValueError, match=message):
        SsocSource(write_workbook(tmp_path, rows)).load()


@pytest.mark.parametrize("code", ["0", "01", "123456", "A", "X1", "X100", "X6000", "x1000", "1.0"])
def test_invalid_codes(tmp_path, code):
    with pytest.raises(ValueError, match="row 4: invalid SSOC code"):
        SsocSource(write_workbook(tmp_path, [(code, "Invalid")])).load()


def test_missing_sheet(tmp_path):
    with pytest.raises(ValueError, match="missing sheet"):
        SsocSource(write_workbook(tmp_path, [], sheet="Wrong sheet")).load()


def test_missing_headers(tmp_path):
    with pytest.raises(ValueError, match="missing headers"):
        SsocSource(write_workbook(tmp_path, [], headers=("Code", "Title"))).load()


def test_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        SsocSource(tmp_path / "missing.xlsx").load()
