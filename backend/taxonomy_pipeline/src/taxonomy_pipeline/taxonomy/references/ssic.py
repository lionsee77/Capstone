"""The official SSIC 2025 classification structure, read by `workbook.ReferenceSource`.

Usage: ``SsicSource(path).load().at_level("division")``. Classification
records retain their own hierarchy rather than being coerced into taxonomy
categories. Registered in the pipeline as dataset type ``ssic``.
"""
from __future__ import annotations

import re
from typing import ClassVar, Literal

from .workbook import ClassificationReference, ReferenceRecord, ReferenceSource

SsicLevel = Literal["section", "division", "group", "class", "subclass"]
LEVELS: tuple[SsicLevel, ...] = ("section", "division", "group", "class", "subclass")

SHEET = "SSIC 2025 Structure"
HEADERS = ("SSIC 2025", "SSIC 2025 Title")


class SsicRecord(ReferenceRecord):
    level: SsicLevel


class SsicReference(ClassificationReference):
    RECORD: ClassVar[type[ReferenceRecord]] = SsicRecord
    LEVELS: ClassVar[tuple[str, ...]] = LEVELS
    SHEET: ClassVar[str] = SHEET
    HEADERS: ClassVar[tuple[str, str]] = HEADERS

    system: Literal["SSIC"] = "SSIC"
    version: Literal["2025"] = "2025"
    records: list[SsicRecord]

    @classmethod
    def place(cls, code: str, top: str | None) -> tuple[str, str | None] | None:
        """A letter is a section; 2-5 digits are division..subclass. A division's
        code doesn't encode its section, so it sits under the last section read."""
        if re.fullmatch(r"[A-Z]", code):
            return "section", None
        if re.fullmatch(r"[0-9]{2,5}", code):
            level = LEVELS[len(code) - 1]
            return level, top if level == "division" else code[:-1]
        return None


class SsicSource(ReferenceSource):
    reference = SsicReference
