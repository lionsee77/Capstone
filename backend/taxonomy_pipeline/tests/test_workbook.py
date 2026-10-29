from __future__ import annotations

from typing import ClassVar, Literal

import pytest
from openpyxl import Workbook

from taxonomy_pipeline.taxonomy.references.workbook import ClassificationReference, ReferenceRecord, ReferenceSource


class ToyReference(ClassificationReference):
    """Two levels: one letter, then that letter plus one digit."""

    RECORD: ClassVar[type[ReferenceRecord]] = ReferenceRecord
    LEVELS: ClassVar[tuple[str, ...]] = ("letter", "digit")
    SHEET: ClassVar[str] = "Toy"
    HEADERS: ClassVar[tuple[str, str]] = ("Code", "Title")

    system: Literal["TOY"] = "TOY"
    version: Literal["1"] = "1"

    @classmethod
    def place(cls, code: str, top: str | None) -> tuple[str, str | None] | None:
        if len(code) == 1 and code.isalpha():
            return "letter", None
        if len(code) == 2 and code[1].isdigit():
            return "digit", code[0]
        return None


class ToySource(ReferenceSource):
    reference = ToyReference


def _workbook(tmp_path, rows):
    path = tmp_path / "toy.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Toy"
    worksheet.append(["Code", "Title"])
    for row in rows:
        worksheet.append(row)
    workbook.save(path)
    return path


def test_a_new_code_system_only_declares_its_levels_and_placement_rule(tmp_path) -> None:
    reference = ToySource(_workbook(tmp_path, [["A", "Alpha"], ["A1", "Alpha one"], [None, None]])).load()
    assert reference.system == "TOY"
    assert reference.counts() == {"letter": 1, "digit": 1}
    assert reference.at_level("digit")[0].parent_code == "A"


@pytest.mark.parametrize(("rows", "message"), [
    ([["1", "Bad"]], "invalid TOY code '1'"),
    ([["B1", "Orphan"]], "missing parent 'B'"),
    ([["A", "Alpha"], ["A", "Again"]], "duplicate TOY code 'A'"),
    ([["A", None]], "missing title"),
    ([[7, "Number"]], "code must be text"),
    ([], "no TOY classification records"),
])
def test_the_shared_checks_name_the_code_system(tmp_path, rows, message) -> None:
    with pytest.raises(ValueError, match=message):
        ToySource(_workbook(tmp_path, rows)).load()
