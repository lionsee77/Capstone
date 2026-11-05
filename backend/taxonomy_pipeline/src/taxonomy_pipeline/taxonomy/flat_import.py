"""Turn one flat table of tags and optional parent links into a taxonomy SQL dump.

Every source the pipeline compares against has to arrive as a 4-table dump
(categories, specialities, tags, speciality_tags) that `sql_dump` can load.
Instead of a one-off generator per source (like
`backend/skillsfuture/generate_skillsfuture_taxonomy.py`), a person writes a
`SourceMapping` YAML naming which column holds each level, and this module
does the rest: read, filter, deduplicate, assign IDs, write.

A level the table only encodes in a code (O*NET's SOC major group is the
first two digits of `15-2051.00`) is mapped as a `DerivedColumn`: a code
prefix looked up in a `code,label` CSV, so it needs no script. A source that
comes as several linked files (ESCO, SkillsFuture) is first flattened into one
such table by a short source-specific script; the joins are where that
source's content decisions live, so they stay in code rather than in the
mapping. Each tag's description (if mapped) is written to a
separate tag-context JSON for Case B's `tag_context` provider, since the
client schema's `tags` table has no description column.


                 flat_import.py  (WRITER)                     sql_dump.py  (READER)
Software Skills.xlsx ──────────────────────► onet_taxonomy.sql ──────────────────────► TaxonomyDataset
 + onet_mapping.yaml   read, filter, dedupe,                      parse INSERTs,         (in memory,
                       give IDs, write SQL text                    map table names        used by metrics)


"""
from __future__ import annotations

import csv
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from openpyxl import load_workbook
from pydantic import BaseModel, Field, model_validator

from .sql_dump import collation_key, sql_literal

# Concept -> columns written, matching what `integrate` writes for new rows.
COLUMNS = {
    "categories": ("id", "name", "service_id", "status"),
    "specialities": ("id", "name", "category_id", "status"),
    "tags": ("id", "name"),
    "speciality_tags": ("id", "speciality_id", "tag_id"),
}
# First ID per table, as in the SkillsFuture dump. IDs only need to be unique within a table,
# so a table outgrowing its range is harmless; the offsets just keep a source's IDs recognisable.
ID_OFFSETS = {"categories": 1000, "specialities": 2000, "tags": 3000, "speciality_tags": 4000}
ACTIVE_STATUS = "1"
# Rows per INSERT statement, so a large source stays well under MySQL's packet limit.
INSERT_CHUNK = 1000


class DerivedColumn(BaseModel):
    """A level read from the first `prefix` characters of a code column, named via a `code,label` CSV.

    For code systems whose parent group is the code's leading digits (SOC,
    ISCO, SSOC, SSIC). `labels` is resolved against the mapping file.
    """

    column: str
    prefix: int = Field(ge=1)
    labels: str


class SourceColumns(BaseModel):
    """Which column of the flat table holds each level; `tag_description` is optional Case B context.

    Each level is a column name, or a `DerivedColumn` computed from one.
    """

    category: str | DerivedColumn
    speciality: str | DerivedColumn
    tag: str | DerivedColumn
    tag_description: str | DerivedColumn | None = None

    def specs(self) -> dict[str, str | DerivedColumn]:
        """Each mapped level and how to read it, leaving out an unmapped description."""
        return {level: spec for level, spec in self if spec is not None}


class SourceMapping(BaseModel):
    """What a person fills in to import a flat source: where it is and which column is which.

    `name` becomes the dataset name in pipeline configs and the default table
    prefix (`<name>_categories`, ...). Every category goes under one baseline
    service (`service_id`), or under the integer service ID in `service_column`.
    `filters` keeps only rows whose column equals the value (or one of the values).
    """

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    display_name: str
    source: str
    sheet: str | None = None
    columns: SourceColumns
    service_id: int | None = None
    service_column: str | None = None
    filters: dict[str, str | list[str]] = Field(default_factory=dict)
    table_prefix: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")

    @model_validator(mode="after")
    def _one_service_source(self) -> SourceMapping:
        if (self.service_id is None) == (self.service_column is None):
            raise ValueError("give exactly one of service_id or service_column")
        return self

    @property
    def table_map(self) -> dict[str, str]:
        prefix = self.table_prefix if self.table_prefix is not None else f"{self.name}_"
        return {concept: f"{prefix}{concept}" for concept in COLUMNS}


def load_mapping(path: str | Path) -> SourceMapping:
    """Load a mapping YAML; a relative `source` is resolved against the mapping file's directory."""
    path = Path(path)
    mapping = SourceMapping.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if not Path(mapping.source).is_absolute():
        mapping.source = str((path.parent / mapping.source).resolve())
    for spec in mapping.columns.specs().values():
        if isinstance(spec, DerivedColumn) and not Path(spec.labels).is_absolute():
            spec.labels = str((path.parent / spec.labels).resolve())
    return mapping


def column_key(level: str, spec: str | DerivedColumn) -> str:
    """The row key `read_rows` stores a level under: its column, or `<level>` for a derived one."""
    return spec if isinstance(spec, str) else f"<{level}>"


def load_labels(path: str | Path) -> dict[str, str]:
    """A `code,label` CSV as {code: label}; refuses missing headers and repeated codes."""
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not {"code", "label"} <= set(reader.fieldnames or []):
            raise ValueError(f"{path} needs 'code' and 'label' headers; it has {reader.fieldnames}")
        labels: dict[str, str] = {}
        for row in reader:
            code = text(row["code"])
            if code in labels:
                raise ValueError(f"{path} lists code {code!r} more than once")
            labels[code] = text(row["label"])
    return labels


def unlabelled_prefixes(rows: list[dict[str, str]], mapping: SourceMapping) -> list[str]:
    """Code prefixes in `rows` that a derived level's labels file has no label for (those rows get skipped)."""
    missing = set()
    for spec in mapping.columns.specs().values():
        if isinstance(spec, DerivedColumn):
            labels = load_labels(spec.labels)
            missing |= {row[spec.column][:spec.prefix] for row in rows} - set(labels) - {""}
    return sorted(missing)


def text(value: Any) -> str:
    """Collapse whitespace the way the SkillsFuture generator does, so names compare the same."""
    return re.sub(r"\s+", " ", str(value if value is not None else "").strip())


def read_rows(mapping: SourceMapping) -> list[dict[str, str]]:
    """Every row of the source that passes `filters`, with whitespace normalised.

    Derived levels are added under `column_key(level, spec)`. Fails naming the
    available headers when a mapped, derived-from or filtered column is missing.
    """
    headers, rows = _read_table(Path(mapping.source), mapping.sheet)
    specs = mapping.columns.specs()
    wanted = [spec if isinstance(spec, str) else spec.column for spec in specs.values()] + list(mapping.filters)
    if mapping.service_column:
        wanted.append(mapping.service_column)
    missing = [column for column in dict.fromkeys(wanted) if column not in headers]
    if missing:
        raise ValueError(f"{mapping.source} has no column(s) {missing}; its headers are {headers}")
    allowed = {
        column: {text(v) for v in (values if isinstance(values, list) else [values])}
        for column, values in mapping.filters.items()
    }
    kept = [row for row in rows if all(row[column] in values for column, values in allowed.items())]
    for level, spec in specs.items():
        if isinstance(spec, DerivedColumn):
            labels = load_labels(spec.labels)
            for row in kept:
                # An unlabelled prefix leaves the level blank, so the row is skipped and counted like any gap.
                row[column_key(level, spec)] = labels.get(row[spec.column][:spec.prefix], "")
    return kept


def _read_table(path: Path, sheet: str | None) -> tuple[list[str], list[dict[str, str]]]:
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            # The first sheet, not `active`: that is whichever tab was selected when the file was saved.
            values = workbook[sheet] if sheet else workbook.worksheets[0]
            iterator = values.iter_rows(values_only=True)
            headers = [text(h) for h in next(iterator, ())]
            raw = [list(row) for row in iterator]
        finally:
            workbook.close()
    elif path.suffix.lower() in (".csv", ".tsv"):
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream, delimiter="\t" if path.suffix.lower() == ".tsv" else ",")
            headers = [text(h) for h in next(reader, [])]
            raw = list(reader)
    else:
        raise ValueError(f"{path}: unsupported source type {path.suffix!r}; use .csv, .tsv or .xlsx")
    repeated = sorted({h for h in headers if h and headers.count(h) > 1})
    if repeated:
        # After whitespace cleanup each header is a dict key, so a repeat would silently keep only its last column.
        raise ValueError(f"{path} has repeated column header(s) {repeated} (after trimming spaces); rename them")
    rows = [
        {header: text(value) for header, value in zip(headers, row, strict=False)}
        for row in raw if any(text(value) for value in row)
    ]
    for row in rows:
        for header in headers:
            row.setdefault(header, "")
    return headers, rows


@dataclass
class FlatTaxonomy:
    """The four tables built from a flat source, each tag's description, skipped rows and merged tag spellings."""

    tables: dict[str, list[dict[str, Any]]]
    tag_context: dict[str, dict[str, str]] = field(default_factory=dict)
    skipped_rows: int = 0
    # Kept spelling -> the other spellings folded into it ("Python" <- ["python"]).
    merged_tags: dict[str, list[str]] = field(default_factory=dict)

    @property
    def counts(self) -> dict[str, int]:
        return {concept: len(rows) for concept, rows in self.tables.items()}


def build_flat_taxonomy(rows: list[dict[str, str]], mapping: SourceMapping) -> FlatTaxonomy:
    """Deduplicate the rows into categories, specialities, tags and links with stable IDs.

    A category is one (service, name); a speciality one name within a category;
    a tag one name across the whole source, linked to every speciality it
    appears under. Tag names are compared like the client schema's unique
    `tags.name` (`collation_key`: case- and accent-insensitive), so "Python" and
    "python" are one tag, spelled as its most common form (ties: the first in
    sorted order); otherwise MySQL would refuse the dump. IDs follow sorted
    names, so the same input always gets the same IDs. Only rows without a tag
    are skipped. Incomplete parent paths retain the tag and context without
    manufacturing empty hierarchy nodes or invalid links.
    """
    keys = {level: column_key(level, spec) for level, spec in mapping.columns.specs().items()}
    links: set[tuple[tuple[int, str], str, str]] = set()
    descriptions: dict[str, str] = {}
    spellings: dict[str, Counter[str]] = {}
    skipped = 0
    for row in rows:
        category, speciality, tag = row[keys["category"]], row[keys["speciality"]], row[keys["tag"]]
        service = _service_id(row, mapping)
        if not tag:
            skipped += 1
            continue
        tag_key = collation_key(tag)
        spellings.setdefault(tag_key, Counter())[tag] += 1
        if category and speciality and service is not None:
            links.add(((service, category), speciality, tag_key))
        description = row.get(keys["tag_description"], "") if "tag_description" in keys else ""
        if description:
            descriptions.setdefault(tag_key, description)

    spelling = {key: min(counts, key=lambda name: (-counts[name], name)) for key, counts in spellings.items()}
    merged = {
        spelling[key]: sorted(set(counts) - {spelling[key]})
        for key, counts in spellings.items() if len(counts) > 1
    }
    category_keys = sorted({category for category, _, _ in links})
    speciality_keys = sorted({(category, speciality) for category, speciality, _ in links})
    tag_keys = sorted(spelling, key=lambda key: (spelling[key], key))
    category_id = {key: ID_OFFSETS["categories"] + i for i, key in enumerate(category_keys)}
    speciality_id = {key: ID_OFFSETS["specialities"] + i for i, key in enumerate(speciality_keys)}
    tag_id = {key: ID_OFFSETS["tags"] + i for i, key in enumerate(tag_keys)}
    link_ids = sorted({(speciality_id[(category, speciality)], tag_id[tag]) for category, speciality, tag in links})

    # build the tables
    tables = {
        "categories": [
            {"id": category_id[key], "name": key[1], "service_id": key[0], "status": ACTIVE_STATUS}
            for key in category_keys
        ],
        "specialities": [
            {"id": speciality_id[key], "name": key[1], "category_id": category_id[key[0]], "status": ACTIVE_STATUS}
            for key in speciality_keys
        ],
        "tags": [{"id": tag_id[key], "name": spelling[key]} for key in tag_keys],
        "speciality_tags": [
            {"id": ID_OFFSETS["speciality_tags"] + i, "speciality_id": sid, "tag_id": tid}
            for i, (sid, tid) in enumerate(link_ids)
        ],
    }
    context = {str(tag_id[key]): {"description": descriptions[key]} for key in tag_keys if key in descriptions}
    return FlatTaxonomy(tables=tables, tag_context=context, skipped_rows=skipped, merged_tags=merged)


def _service_id(row: dict[str, str], mapping: SourceMapping) -> int | None:
    if mapping.service_id is not None:
        return mapping.service_id
    try:
        return int(row[mapping.service_column or ""])
    except ValueError:
        return None


def dump_sql(taxonomy: FlatTaxonomy, mapping: SourceMapping) -> str:
    """The SQL dump text: CREATE ... LIKE the client tables, then INSERTs in chunks, parents first."""
    table_map = mapping.table_map
    lines = [
        f"-- {mapping.display_name} taxonomy generated by `taxonomy-pipeline import-source`; review before importing.",
        "-- Same session settings as the client's phpMyAdmin dumps: clears NO_BACKSLASH_ESCAPES,",
        "-- which the string escaping below relies on.",
        'SET SQL_MODE = "NO_AUTO_VALUE_ON_ZERO";',
        "SET NAMES utf8mb4;",
        "START TRANSACTION;",
        "",
    ]
    lines += [f"CREATE TABLE IF NOT EXISTS `{table_map[c]}` LIKE `{c}`;" for c in COLUMNS]
    for concept, columns in COLUMNS.items():
        rows = taxonomy.tables[concept]
        header = ", ".join(f"`{c}`" for c in columns)
        for start in range(0, len(rows), INSERT_CHUNK):
            values = ",\n".join(
                "(" + ", ".join(sql_literal(row[c]) for c in columns) + ")" for row in rows[start:start + INSERT_CHUNK]
            )
            lines += ["", f"INSERT INTO `{table_map[concept]}` ({header}) VALUES", f"{values};"]
    lines += ["", "COMMIT;", ""]
    return "\n".join(lines)


def dump_tag_context(taxonomy: FlatTaxonomy, mapping: SourceMapping) -> str:
    """The tag-context JSON the `tag_context` Case B provider reads."""
    payload = {"source": mapping.name, "display_name": mapping.display_name, "tags": taxonomy.tag_context}
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
