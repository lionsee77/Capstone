"""Shared by sources that publish a preferred label with alternative labels (ESCO, O*NET).

A row's preferred label is matched to concept names the way every stage matches names
(`SynonymIndex.names`, keyed by `match_key`); each of its alternative labels is then
suggested for that concept. A label naming several of our concepts (duplicate rows) is
skipped rather than guessed: such a name is already ambiguous, so attaching synonyms to
it would only spread the ambiguity.

`read_table` reads the downloads: CSV, tab-separated text (no quoting: O*NET's text
files are plain tab-separated, and a field starting with `"` must not swallow the
rows after it) or .xlsx (closed after reading, since the API is long-running). A file
that can't be parsed is a ValueError naming it, so adapters report it as a bad request.
"""
from __future__ import annotations

import csv
import zipfile
from collections.abc import Iterable
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from ...matching.names import match_key
from ...taxonomy.base import TaxonomyDataset
from ..resolver import SynonymIndex
from .base import Suggestion


def suggest_from_labels(
    taxonomy: TaxonomyDataset, rows: Iterable[tuple[str, list[str], str]],
) -> list[Suggestion]:
    """`rows` are (preferred label, alternative labels, evidence) from the source file."""
    names = SynonymIndex(taxonomy).names
    suggestions = []
    for preferred, alternatives, evidence in rows:
        matches = names.get(match_key(preferred), [])
        if len(matches) != 1:
            continue
        level, concept_id, _ = matches[0]
        for alternative in dict.fromkeys(label.strip() for label in alternatives):
            if alternative and match_key(alternative) != match_key(preferred):
                suggestions.append(Suggestion(
                    concept_level=level, concept_id=concept_id, term=alternative, evidence=evidence,
                ))
    return suggestions


def read_table(path: str | Path, columns: tuple[str, ...]) -> list[dict[str, str]]:
    """Rows of a CSV, tab-separated text or .xlsx file, keeping only `columns` (which must all exist)."""
    path = Path(path)
    try:
        header, records = _read_xlsx(path) if path.suffix.lower() == ".xlsx" else _read_text(path)
    except (zipfile.BadZipFile, InvalidFileException, csv.Error, UnicodeDecodeError, KeyError) as exc:
        kind = path.suffix or "text"
        raise ValueError(f"{path.name} couldn't be read as {kind}: {type(exc).__name__}: {exc}") from exc
    missing = [column for column in columns if column not in header]
    if missing:
        raise ValueError(f"{path.name} has no column(s) {missing}; its header is {header}")
    return [{column: record.get(column, "") for column in columns} for record in records]


def _read_xlsx(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        rows = workbook.worksheets[0].iter_rows(values_only=True)
        header = [str(cell or "").strip() for cell in next(rows, [])]
        records = [dict(zip(header, ("" if cell is None else str(cell) for cell in row), strict=False))
                   for row in rows]
    finally:
        workbook.close()
    return header, records


def _read_text(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() in (".txt", ".tsv"):
        reader = csv.DictReader(text.splitlines(keepends=True), delimiter="\t", quoting=csv.QUOTE_NONE)
    else:
        reader = csv.DictReader(text.splitlines(keepends=True))
    header = [name.strip() for name in reader.fieldnames or []]
    records = [{key.strip(): value or "" for key, value in row.items() if key} for row in reader]
    return header, records
