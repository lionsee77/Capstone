"""Load a TaxonomyDataset straight from one or more plain SQL dump files -
phpMyAdmin-style exports (`backend/sql-files-nus/*.sql`) or a bare generated
file (`backend/skillsfuture/skillsfuture_taxonomy.sql`). No database
connection needed; both formats are just `INSERT INTO ... VALUES (...);`
statements with an explicit column list, which is all this parser assumes.

                 flat_import.py  (WRITER)                     sql_dump.py  (READER)
Software Skills.xlsx ──────────────────────► onet_taxonomy.sql ──────────────────────► TaxonomyDataset
 + onet_mapping.yaml   read, filter, dedupe,                      parse INSERTs,         (in memory,
                       give IDs, write SQL text                    map table names        used by metrics)
reads any .sql dump file into memory as a TaxonomyDataset, without connecting to a database.

"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .base import CONCEPTS, TaxonomyDataset

_INSERT_HEADER = re.compile(
    r"INSERT INTO\s+`?(?P<table>\w+)`?\s*\((?P<columns>[^)]*)\)\s*VALUES\s*",
    re.IGNORECASE,
)
_CREATE_HEADER = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?(?P<table>\w+)`?",
    re.IGNORECASE,
)

# Maps a single escaped character (the char after a backslash in a SQL
# string literal) to what it decodes to. Anything not listed here just
# drops the backslash (MySQL's own behaviour for an unrecognized escape).
_ESCAPE_MAP = {"0": "\0", "b": "\b", "n": "\n", "r": "\r", "t": "\t", "Z": "\x1a"}


def _skip_whitespace_and_comments(sql_text: str, start: int = 0) -> int:
    """Return the first non-comment token at a statement boundary."""
    i, n = start, len(sql_text)
    while i < n:
        if sql_text[i].isspace():
            i += 1
        elif sql_text.startswith("--", i) and (i + 2 == n or sql_text[i + 2].isspace()):
            newline = sql_text.find("\n", i + 2)
            i = n if newline == -1 else newline + 1
        elif sql_text[i] == "#":
            newline = sql_text.find("\n", i + 1)
            i = n if newline == -1 else newline + 1
        elif sql_text.startswith("/*", i):
            closing = sql_text.find("*/", i + 2)
            i = n if closing == -1 else closing + 2
        else:
            return i
    return i


def _iter_sql_statements(sql_text: str) -> Iterator[tuple[int, int]]:
    """Yield ranges for SQL statements, splitting only outside strings/comments."""
    start = 0
    depth = 0
    in_string = False
    quote_char = ""
    i, n = 0, len(sql_text)
    while i < n:
        char = sql_text[i]
        if in_string:
            if char == "\\" and i + 1 < n:
                i += 2
                continue
            if char == quote_char:
                if i + 1 < n and sql_text[i + 1] == quote_char:
                    i += 2
                    continue
                in_string = False
            i += 1
            continue
        if sql_text.startswith("--", i) and (i + 2 == n or sql_text[i + 2].isspace()):
            newline = sql_text.find("\n", i + 2)
            i = n if newline == -1 else newline + 1
            continue
        if char == "#":
            newline = sql_text.find("\n", i + 1)
            i = n if newline == -1 else newline + 1
            continue
        if sql_text.startswith("/*", i):
            closing = sql_text.find("*/", i + 2)
            i = n if closing == -1 else closing + 2
            continue
        if char in ("'", '"'):
            in_string = True
            quote_char = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == ";" and depth == 0:
            yield start, i
            start = i + 1
        i += 1
    if sql_text[start:].strip():
        yield start, n


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


def collation_key(name: Any) -> str:
    """Approximate MySQL's utf8mb4_general_ci comparison: case- and accent-insensitive, trailing spaces ignored.

    The client schema's `tags.name` is unique under that collation, so every
    writer of taxonomy dumps (`integrate`, `flat_import`) checks names with it.
    It lower-cases rather than `casefold()`s: general_ci weighs each character on
    its own, so `ß` equals a single `s` ("Maße" = "Mase"), not casefold's "ss".
    """
    decomposed = unicodedata.normalize("NFKD", str(name))
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return stripped.lower().replace("ß", "s").rstrip(" ")


def sql_literal(value: Any) -> str:
    """A MySQL literal that `sql_dump` decodes back to the same value.

    Quotes are doubled (valid in every SQL mode). Backslash escapes for
    backslash and control characters rely on the default mode, which the
    files written with it (`integrate`'s `additions.sql`, `flat_import`'s
    dumps) set in their header like the client's own dumps do. Kept next to
    the parser so the two can't drift apart.
    """
    if value is None:
        return "NULL"
    if type(value) is int:
        return str(value)
    escaped = (
        str(value).replace("\\", "\\\\").replace("'", "''").replace("\0", "\\0")
        .replace("\n", "\\n").replace("\r", "\\r").replace("\x1a", "\\Z")
    )
    return f"'{escaped}'"


def iter_insert_statements(sql_text: str) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    """Yield (table_name, rows) for every INSERT INTO statement in `sql_text`.

    Statements are identified with a quote/comment-aware scan before an
    INSERT header is matched. This prevents INSERT-like text in a string
    field or SQL comment from being mistaken for a second statement.
    """
    for start, end in _iter_sql_statements(sql_text):
        statement_start = _skip_whitespace_and_comments(sql_text, start)
        header = _INSERT_HEADER.match(sql_text, statement_start, end)
        if header is None:
            continue
        table = header.group("table")
        columns = [c.strip().strip("`\"") for c in header.group("columns").split(",")]
        values_text = sql_text[header.end():end]

        # INSERT INTO `onet_categories` (`id`, `name`, `service_id`, `status`) VALUES
        # (1000, 'Architecture and Engineering', 2, '1'),
        # (1001, 'Arts, Design, Entertainment, Sports, and Media', 2, '1');
        rows = []
        for tuple_text in _split_top_level(values_text, ","): # split the ',' btwn each entry
            tuple_text = tuple_text.strip()
            if not tuple_text:
                continue
            inner = (
                tuple_text[1:-1]
                if tuple_text.startswith("(") and tuple_text.endswith(")")
                else tuple_text
            )
            fields = _split_top_level(inner, ",")
            rows.append({col: _convert_field(val) for col, val in zip(columns, fields, strict=True)})
        yield table, rows


def iter_created_tables(sql_text: str) -> Iterator[str]:
    """Yield the table name of every CREATE TABLE statement in `sql_text`,
    using the same quote/comment-aware scan as `iter_insert_statements`.
    Lets a loader tell an empty table (created, no INSERTs) from a missing one.
    """
    for start, end in _iter_sql_statements(sql_text):
        statement_start = _skip_whitespace_and_comments(sql_text, start)
        header = _CREATE_HEADER.match(sql_text, statement_start, end)
        if header is not None:
            yield header.group("table")


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
            for table in iter_created_tables(text):
                tables.setdefault(table, [])
            for table, rows in iter_insert_statements(text):
                tables.setdefault(table, []).extend(rows)

        # Only pass concepts whose table the dump actually has (created or
        # inserted into), so TaxonomyDataset.has_table() can tell a missing
        # table from an empty one.
        data = {
            concept: tables[table_name]
            for concept, table_name in self.table_map.items()
            if table_name in tables
        }
        return TaxonomyDataset(name=self.name, **data)
