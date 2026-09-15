"""Standalone loader for the official SSIC 2025 classification structure.

Usage: ``SsicSource(path).load().at_level("division")``. Classification
records retain their own hierarchy rather than being coerced into taxonomy
categories. Registered in the pipeline as dataset type ``ssic``.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from openpyxl import load_workbook
from pydantic import BaseModel

SsicLevel = Literal["section", "division", "group", "class", "subclass"]
LEVELS: tuple[SsicLevel, ...] = ("section", "division", "group", "class", "subclass")
SHEET = "SSIC 2025 Structure"
HEADERS = ("SSIC 2025", "SSIC 2025 Title")


class SsicRecord(BaseModel):
    code: str
    title: str
    level: SsicLevel
    parent_code: str | None


class SsicReference(BaseModel):
    system: Literal["SSIC"] = "SSIC"
    version: Literal["2025"] = "2025"
    source_path: Path
    records: list[SsicRecord]

    def counts(self) -> dict[str, int]:
        return {level: len(self.at_level(level)) for level in LEVELS}

    def at_level(self, level: SsicLevel) -> list[SsicRecord]:
        """Select one level without discarding the stored full hierarchy."""
        if level not in LEVELS:
            raise ValueError(f"Unsupported SSIC level {level!r}; expected one of {LEVELS}")
        return [record for record in self.records if record.level == level]


class SsicSource:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> SsicReference:
        """Read and validate the complete hierarchy; never modify the workbook.

        Codes must be stored as text: guessing missing zeros in numeric Excel
        cells could silently change a classification's level or identity.
        """
        workbook = load_workbook(self.path, read_only=True, data_only=True)
        try:
            if SHEET not in workbook.sheetnames:
                raise ValueError(f"{self.path}: missing sheet {SHEET!r}")
            rows = enumerate(
                workbook[SHEET].iter_rows(min_col=1, max_col=2, values_only=True), start=1
            )
            for _, values in rows:
                if tuple(str(value or "").strip() for value in values) == HEADERS:
                    break
            else:
                raise ValueError(f"{self.path}: missing headers {HEADERS!r}")

            records: list[SsicRecord] = []
            seen: set[str] = set()
            section: str | None = None
            for row_number, (raw_code, raw_title) in rows:
                code = str(raw_code).strip() if raw_code is not None else ""
                title = str(raw_title).strip() if raw_title is not None else ""
                if not code and not title:
                    continue

                context = f"{self.path}: row {row_number}"
                if not isinstance(raw_code, str):
                    raise ValueError(f"{context}: code must be text to preserve leading zeros")
                if re.fullmatch(r"[A-Z]", code):
                    level: SsicLevel = "section"
                    parent = None
                elif re.fullmatch(r"[0-9]{2,5}", code):
                    level = LEVELS[len(code) - 1]
                    parent = section if level == "division" else code[:-1]
                    if parent is None or parent not in seen:
                        raise ValueError(f"{context}: missing parent {parent!r} for code {code!r}")
                else:
                    raise ValueError(f"{context}: invalid SSIC code {code!r}")
                if not title:
                    raise ValueError(f"{context}: missing title for code {code!r}")
                if code in seen:
                    raise ValueError(f"{context}: duplicate SSIC code {code!r}")

                records.append(SsicRecord(code=code, title=title, level=level, parent_code=parent))
                seen.add(code)
                if level == "section":
                    section = code

            if not records:
                raise ValueError(f"{self.path}: no SSIC classification records found")
            return SsicReference(source_path=self.path.resolve(), records=records)
        finally:
            workbook.close()
