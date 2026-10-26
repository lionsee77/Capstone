"""The one loader for official classification-structure workbooks (SSIC, SSOC).

Both workbooks are a sheet with a header row of (code, title) followed by one
row per classification, each code's level and parent following from its shape.
Only that last part differs between systems, so a reference type here declares
its levels, sheet, headers and a `place(code, top)` rule, and `ReferenceSource`
does the reading and validation. A new code system is one module with a
`ClassificationReference` subclass plus one registry line in `taxonomy/__init__.py`.
"""
from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from openpyxl import load_workbook
from pydantic import BaseModel


class ReferenceRecord(BaseModel):
    code: str
    title: str
    level: str
    parent_code: str | None


class ClassificationReference(BaseModel):
    """A full classification hierarchy; subclasses pin `system`, `version` and the record type."""

    RECORD: ClassVar[type[ReferenceRecord]]
    LEVELS: ClassVar[tuple[str, ...]]
    SHEET: ClassVar[str]
    HEADERS: ClassVar[tuple[str, str]]

    system: str
    version: str
    source_path: Path
    records: list[ReferenceRecord]

    @classmethod
    def place(cls, code: str, top: str | None) -> tuple[str, str | None] | None:
        """The (level, parent code) a code's shape gives it, or None for an invalid code.

        `top` is the most recent top-level code read so far, for systems whose
        second level sits under it without encoding it in the code.
        """
        raise NotImplementedError

    def counts(self) -> dict[str, int]:
        return {level: len(self.at_level(level)) for level in self.LEVELS}

    def at_level(self, level: str) -> list[ReferenceRecord]:
        """Select one level without discarding the stored full hierarchy."""
        if level not in self.LEVELS:
            raise ValueError(f"Unsupported {self.system} level {level!r}; expected one of {self.LEVELS}")
        return [record for record in self.records if record.level == level]


class ReferenceSource:
    """Reads one workbook into `reference`; subclasses set which reference type."""

    reference: ClassVar[type[ClassificationReference]]

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> ClassificationReference:
        """Read and validate the complete hierarchy; never modify the workbook.

        Codes must be stored as text: guessing missing zeros in numeric Excel
        cells could silently change a classification's level or identity.
        """
        spec = self.reference
        system = spec.model_fields["system"].default
        workbook = load_workbook(self.path, read_only=True, data_only=True)
        try:
            if spec.SHEET not in workbook.sheetnames:
                raise ValueError(f"{self.path}: missing sheet {spec.SHEET!r}")
            rows = enumerate(workbook[spec.SHEET].iter_rows(min_col=1, max_col=2, values_only=True), start=1)
            for _, values in rows:
                if tuple(str(value or "").strip() for value in values) == spec.HEADERS:
                    break
            else:
                raise ValueError(f"{self.path}: missing headers {spec.HEADERS!r}")

            records = []
            seen: set[str] = set()
            top: str | None = None
            for row_number, (raw_code, raw_title) in rows:
                code = str(raw_code).strip() if raw_code is not None else ""
                title = str(raw_title).strip() if raw_title is not None else ""
                if not code and not title:
                    continue

                context = f"{self.path}: row {row_number}"
                if not isinstance(raw_code, str):
                    raise ValueError(f"{context}: code must be text to preserve leading zeros")
                placed = spec.place(code, top)
                if placed is None:
                    raise ValueError(f"{context}: invalid {system} code {code!r}")
                level, parent = placed
                if not title:
                    raise ValueError(f"{context}: missing title for code {code!r}")
                if code in seen:
                    raise ValueError(f"{context}: duplicate {system} code {code!r}")
                if level != spec.LEVELS[0] and (parent is None or parent not in seen):
                    raise ValueError(f"{context}: missing parent {parent!r} for code {code!r}")

                records.append(spec.RECORD(code=code, title=title, level=level, parent_code=parent))
                seen.add(code)
                if level == spec.LEVELS[0]:
                    top = code

            if not records:
                raise ValueError(f"{self.path}: no {system} classification records found")
            return spec(source_path=self.path.resolve(), records=records)
        finally:
            workbook.close()
