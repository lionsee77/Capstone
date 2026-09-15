"""Standalone loader for the official SSOC 2024 classification structure.

Usage: ``SsocSource(path).load().at_level("minor_group")``. The special
occupations X1000 through X5000 belong directly to major group X, without
intermediate groups. Registered in the pipeline as dataset type ``ssoc``.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from openpyxl import load_workbook
from pydantic import BaseModel

SsocLevel = Literal["major_group", "sub_major_group", "minor_group", "unit_group", "occupation"]
LEVELS: tuple[SsocLevel, ...] = (
    "major_group", "sub_major_group", "minor_group", "unit_group", "occupation",
)
SHEET = "SSOC 2024 Structure"
HEADERS = ("SSOC 2024", "SSOC 2024 Title")


class SsocRecord(BaseModel):
    code: str
    title: str
    level: SsocLevel
    parent_code: str | None


class SsocReference(BaseModel):
    system: Literal["SSOC"] = "SSOC"
    version: Literal["2024"] = "2024"
    source_path: Path
    records: list[SsocRecord]

    def counts(self) -> dict[str, int]:
        return {level: len(self.at_level(level)) for level in LEVELS}

    def at_level(self, level: SsocLevel) -> list[SsocRecord]:
        """Select one level without discarding the stored full hierarchy."""
        if level not in LEVELS:
            raise ValueError(f"Unsupported SSOC level {level!r}; expected one of {LEVELS}")
        return [record for record in self.records if record.level == level]


class SsocSource:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> SsocReference:
        """Read the complete hierarchy, requiring text codes as in the official file."""
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

            records: list[SsocRecord] = []
            seen: set[str] = set()
            for row_number, (raw_code, raw_title) in rows:
                code = str(raw_code).strip() if raw_code is not None else ""
                title = str(raw_title).strip() if raw_title is not None else ""
                if not code and not title:
                    continue

                context = f"{self.path}: row {row_number}"
                if not isinstance(raw_code, str):
                    raise ValueError(f"{context}: code must be text")
                if code == "X":
                    level: SsocLevel = "major_group"
                    parent = None
                elif re.fullmatch(r"X[1-5]000", code):
                    level = "occupation"
                    parent = "X"
                elif re.fullmatch(r"[1-9][0-9]{0,4}", code):
                    level = LEVELS[len(code) - 1]
                    parent = code[:-1] if len(code) > 1 else None
                else:
                    raise ValueError(f"{context}: invalid SSOC code {code!r}")

                if not title:
                    raise ValueError(f"{context}: missing title for code {code!r}")
                if code in seen:
                    raise ValueError(f"{context}: duplicate SSOC code {code!r}")
                if parent is not None and parent not in seen:
                    raise ValueError(f"{context}: missing parent {parent!r} for code {code!r}")

                records.append(SsocRecord(code=code, title=title, level=level, parent_code=parent))
                seen.add(code)

            if not records:
                raise ValueError(f"{self.path}: no SSOC classification records found")
            return SsocReference(source_path=self.path.resolve(), records=records)
        finally:
            workbook.close()
