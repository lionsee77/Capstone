"""Shared by sources that publish a preferred label with alternative labels (ESCO, O*NET).

A row's preferred label is matched to concept names after `normalize`; each of its
alternative labels is then suggested for that concept. A label naming several of
our concepts (duplicate rows) is skipped rather than guessed: such a name is already
ambiguous, so attaching synonyms to it would only spread the ambiguity.
"""
from __future__ import annotations

import csv
from collections.abc import Iterable
from pathlib import Path

from openpyxl import load_workbook

from ...matching.names import normalize
from ...taxonomy.base import TaxonomyDataset
from .base import Suggestion, concepts_by_name


def suggest_from_labels(
    taxonomy: TaxonomyDataset, rows: Iterable[tuple[str, list[str], str]],
) -> list[Suggestion]:
    """`rows` are (preferred label, alternative labels, evidence) from the source file."""
    names = concepts_by_name(taxonomy)
    suggestions = []
    for preferred, alternatives, evidence in rows:
        matches = names.get(normalize(preferred), [])
        if len(matches) != 1:
            continue
        level, concept_id, _ = matches[0]
        for alternative in dict.fromkeys(label.strip() for label in alternatives):
            if alternative and normalize(alternative) != normalize(preferred):
                suggestions.append(Suggestion(
                    concept_level=level, concept_id=concept_id, term=alternative, evidence=evidence,
                ))
    return suggestions


def read_table(path: str | Path, columns: tuple[str, ...]) -> list[dict[str, str]]:
    """Rows of a CSV, tab-separated text or .xlsx file, keeping only `columns` (which must all exist)."""
    path = Path(path)
    if path.suffix.lower() == ".xlsx":
        sheet = load_workbook(path, read_only=True, data_only=True).worksheets[0]
        rows = sheet.iter_rows(values_only=True)
        header = [str(cell or "").strip() for cell in next(rows, [])]
        records = [dict(zip(header, ("" if cell is None else str(cell) for cell in row), strict=False))
                   for row in rows]
    else:
        text = path.read_text(encoding="utf-8-sig")
        dialect = "excel-tab" if path.suffix.lower() in (".txt", ".tsv") else "excel"
        reader = csv.DictReader(text.splitlines(keepends=True), dialect=dialect)
        header = [name.strip() for name in reader.fieldnames or []]
        records = [{key.strip(): value or "" for key, value in row.items() if key} for row in reader]
    missing = [column for column in columns if column not in header]
    if missing:
        raise ValueError(f"{path.name} has no column(s) {missing}; its header is {header}")
    return [{column: record.get(column, "") for column in columns} for record in records]
