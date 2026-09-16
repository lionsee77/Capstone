"""Load a TaxonomyDataset straight from one or more plain SQL dump files -
phpMyAdmin-style exports (`backend/sql-files-nus/*.sql`) or a bare generated
file (`backend/skillsfuture/skillsfuture_taxonomy.sql`). No database
connection needed; both formats are just `INSERT INTO ... VALUES (...);`
statements with an explicit column list, which is all this parser assumes.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterator

from .base import CONCEPTS, TaxonomyDataset

_INSERT_HEADER = re.compile(
    r"INSERT INTO\s+`?(?P<table>\w+)`?\s*\((?P<columns>[^)]*)\)\s*VALUES\s*",
    re.IGNORECASE,
)

# Maps a single escaped character (the char after a backslash in a SQL
# string literal) to what it decodes to. Anything not listed here just
# drops the backslash (MySQL's own behaviour for an unrecognized escape).
_ESCAPE_MAP = {"0": "\0", "b": "\b", "n": "\n", "r": "\r", "t": "\t", "Z": "\x1a"}


def _split_top_level(text: str, sep: str = ",") -> list[str]:
    """Split `text` on `sep`, ignoring separators inside quoted strings or
    nested parentheses - so a comma inside a `description` field, or between
    two `(...)` row tuples, is handled correctly either way.
    """
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    in_string = False
    quote_char = ""
    i, n = 0, len(text)
    while i < n:
        char = text[i]
        if in_string:
            if char == "\\" and i + 1 < n:
                current.append(char)
                current.append(text[i + 1])
                i += 2
                continue
            if char == quote_char:
                if i + 1 < n and text[i + 1] == quote_char:
                    current.append(char)
                    current.append(text[i + 1])
                    i += 2
                    continue
                in_string = False
            current.append(char)
            i += 1
            continue
        if char in ("'", '"'):
            in_string = True
            quote_char = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == sep and depth == 0:
            parts.append("".join(current))
            current = []
            i += 1
            continue
        current.append(char)
        i += 1
    parts.append("".join(current))
    return parts


def _unescape_sql_string(inner: str, quote: str) -> str:
    """Decode a SQL string literal's body in a single left-to-right pass.

    Doing this as sequential `.replace()` calls (the previous approach) is
    wrong: replacing `\\\\` -> `\\` first can manufacture a fresh `\\n` or
    `\\r` two-char sequence out of a literal backslash that was immediately
    followed by a literal 'n'/'r', which the next `.replace()` call then
    corrupts into a real newline/carriage-return. A single regex pass never
    re-scans output it has already produced, so that can't happen.
    """
    pattern = re.compile(re.escape(quote * 2) + "|\\\\.", re.DOTALL)

    def repl(match: re.Match[str]) -> str:
        token = match.group(0)
        if token == quote * 2:
            return quote
        escaped = token[1]
        if escaped == quote:
            return quote
        return _ESCAPE_MAP.get(escaped, escaped)

    return pattern.sub(repl, inner)


def _convert_field(raw: str) -> Any:
    value = raw.strip()
    if value.upper() == "NULL":
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        quote = value[0]
        inner = value[1:-1]
        return _unescape_sql_string(inner, quote)
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def iter_insert_statements(sql_text: str) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    """Yield (table_name, rows) for every INSERT INTO statement in `sql_text`.

    The terminating `;` is found by hand-scanning (tracking quote/paren
    depth) rather than by regex, so a semicolon inside a quoted text field
    (a category `description`, say) can't truncate a statement early.
    """
    for header in _INSERT_HEADER.finditer(sql_text):
        table = header.group("table")
        columns = [c.strip().strip("`\"") for c in header.group("columns").split(",")]

        i, n = header.end(), len(sql_text)
        depth = 0
        in_string = False
        quote_char = ""
        while i < n:
            char = sql_text[i]
            if in_string:
                if char == "\\":
                    i += 2
                    continue
                if char == quote_char:
                    if i + 1 < n and sql_text[i + 1] == quote_char:
                        i += 2
                        continue
                    in_string = False
                i += 1
                continue
            if char in ("'", '"'):
                in_string = True
                quote_char = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif char == ";" and depth == 0:
                break
            i += 1
        values_text = sql_text[header.end():i]

        rows = []
        for tuple_text in _split_top_level(values_text, ","):
            tuple_text = tuple_text.strip()
            if not tuple_text:
                continue
            inner = (
                tuple_text[1:-1]
                if tuple_text.startswith("(") and tuple_text.endswith(")")
                else tuple_text
            )
            fields = _split_top_level(inner, ",")
            rows.append({col: _convert_field(val) for col, val in zip(columns, fields)})
        yield table, rows


class SqlDumpSource:
    """Reads one or more SQL dump files and maps their tables onto the
    canonical taxonomy concepts via `table_map` (concept -> actual table
    name; concepts not listed default to their own name).
    """

    def __init__(
        self,
        name: str,
        paths: list[str | Path],
        table_map: dict[str, str] | None = None,
    ):
        self.name = name
        self.paths = [Path(p) for p in paths]
        table_map = table_map or {}
        self.table_map = {concept: table_map.get(concept, concept) for concept in CONCEPTS}

    def load(self) -> TaxonomyDataset:
        tables: dict[str, list[dict[str, Any]]] = {}
        for path in self.paths:
            text = path.read_text(encoding="utf-8", errors="replace")
            for table, rows in iter_insert_statements(text):
                tables.setdefault(table, []).extend(rows)

        data = {
            concept: tables.get(table_name, [])
            for concept, table_name in self.table_map.items()
        }
        return TaxonomyDataset(name=self.name, **data)
