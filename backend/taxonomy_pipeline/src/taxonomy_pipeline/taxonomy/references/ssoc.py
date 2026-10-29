"""The official SSOC 2024 classification structure, read by `workbook.ReferenceSource`.

Usage: ``SsocSource(path).load().at_level("minor_group")``. The special
occupations X1000 through X5000 belong directly to major group X, without
intermediate groups. Registered in the pipeline as dataset type ``ssoc``.
"""
from __future__ import annotations

import re
from typing import ClassVar, Literal

from .workbook import ClassificationReference, ReferenceRecord, ReferenceSource

SsocLevel = Literal["major_group", "sub_major_group", "minor_group", "unit_group", "occupation"]
LEVELS: tuple[SsocLevel, ...] = (
    "major_group", "sub_major_group", "minor_group", "unit_group", "occupation",
)

SHEET = "SSOC 2024 Structure"
HEADERS = ("SSOC 2024", "SSOC 2024 Title")


class SsocRecord(ReferenceRecord):
    level: SsocLevel


class SsocReference(ClassificationReference):
    RECORD: ClassVar[type[ReferenceRecord]] = SsocRecord
    LEVELS: ClassVar[tuple[str, ...]] = LEVELS
    SHEET: ClassVar[str] = SHEET
    HEADERS: ClassVar[tuple[str, str]] = HEADERS

    system: Literal["SSOC"] = "SSOC"
    version: Literal["2024"] = "2024"
    records: list[SsocRecord]

    @classmethod
    def place(cls, code: str, top: str | None) -> tuple[str, str | None] | None:
        """Each extra digit is one level down, under the code minus its last digit;
        X and X1000-X5000 are the special major group and its occupations."""
        if code == "X":
            return "major_group", None
        if re.fullmatch(r"X[1-5]000", code):
            return "occupation", "X"
        if re.fullmatch(r"[1-9][0-9]{0,4}", code):
            return LEVELS[len(code) - 1], code[:-1] if len(code) > 1 else None
        return None


class SsocSource(ReferenceSource):
    reference = SsocReference
