"""Validate the SQL produced by generate_skillsfuture_taxonomy.py.

This is a static, read-only check. It does not connect to MySQL or execute SQL.
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import sys
from pathlib import Path


TABLES = {
    "sf_categories": ("id", "name", "service_id", "status"),
    "sf_specialities": ("id", "name", "category_id", "status"),
    "sf_tags": ("id", "name"),
    "sf_speciality_tags": ("id", "speciality_id", "tag_id"),
}


def parse_value_tuple(line: str) -> list[str] | None:
    match = re.match(r"^\((.*)\)[,;]?$", line.strip())
    if not match:
        return None
    reader = csv.reader(io.StringIO(match.group(1)), quotechar="'", skipinitialspace=True)
    values = next(reader)
    return [value.strip() for value in values]


def load_rows(path: Path) -> dict[str, list[list[str]]]:
    rows: dict[str, list[list[str]]] = {table: [] for table in TABLES}
    current = None
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        insert = re.match(r"INSERT INTO (sf_[a-z_]+)\s*\(", line, re.I)
        if insert:
            current = insert.group(1).lower()
            if current not in rows:
                raise ValueError(f"line {line_number}: unexpected table {current}")
            continue
        if current:
            values = parse_value_tuple(line)
            if values is not None:
                rows[current].append(values)
            if line.endswith(";"):
                current = None
    return rows


def duplicates(values: list, label: str, errors: list[str]) -> None:
    seen = {}
    for index, value in enumerate(values, 1):
        if value in seen:
            errors.append(f"duplicate {label}: {value!r} (rows {seen[value]} and {index})")
        else:
            seen[value] = index


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sql", nargs="?", type=Path, default=Path(__file__).with_name("skillsfuture_taxonomy.sql"))
    args = parser.parse_args()
    if not args.sql.exists():
        print(f"ERROR: SQL file not found: {args.sql}", file=sys.stderr)
        print("Generate it first with: python skillsfuture/generate_skillsfuture_taxonomy.py", file=sys.stderr)
        return 2

    try:
        rows = load_rows(args.sql)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    errors: list[str] = []
    warnings: list[str] = []
    counts = {table: len(table_rows) for table, table_rows in rows.items()}
    for table, expected in TABLES.items():
        if not rows[table]:
            errors.append(f"missing or empty INSERT section: {table}")
        for index, values in enumerate(rows[table], 1):
            if len(values) != len(expected):
                errors.append(f"{table} row {index}: expected {len(expected)} values, found {len(values)}")

    categories = {row[0]: row for row in rows["sf_categories"] if len(row) == 4}
    specialities = {row[0]: row for row in rows["sf_specialities"] if len(row) == 4}
    tags = {row[0]: row for row in rows["sf_tags"] if len(row) == 2}

    duplicates([row[0] for row in rows["sf_categories"]], "category ID", errors)
    duplicates([row[1].casefold() for row in rows["sf_categories"]], "category name", errors)
    duplicates([row[0] for row in rows["sf_specialities"]], "speciality ID", errors)
    duplicates([row[0] for row in rows["sf_tags"]], "tag ID", errors)
    duplicates([row[1].casefold() for row in rows["sf_tags"]], "tag name", errors)

    links = rows["sf_speciality_tags"]
    duplicates([(row[1], row[2]) for row in links if len(row) == 3], "speciality/tag relationship", errors)
    for row in rows["sf_specialities"]:
        if len(row) == 4 and row[2] not in categories:
            errors.append(f"speciality {row[0]} references missing category {row[2]}")
    for row in links:
        if len(row) == 3:
            if row[1] not in specialities:
                errors.append(f"link {row[0]} references missing speciality {row[1]}")
            if row[2] not in tags:
                errors.append(f"link {row[0]} references missing tag {row[2]}")

    category_ids_with_roles = {row[2] for row in rows["sf_specialities"] if len(row) == 4}
    speciality_ids_with_tags = {row[1] for row in links if len(row) == 3}
    tag_ids_in_links = {row[2] for row in links if len(row) == 3}
    for category_id in categories:
        if category_id not in category_ids_with_roles:
            errors.append(f"dead-end category {category_id} ({categories[category_id][1]}): no specialities")
    for speciality_id in specialities:
        if speciality_id not in speciality_ids_with_tags:
            errors.append(f"dead-end speciality {speciality_id} ({specialities[speciality_id][1]}): no tags")
    for tag_id in tags:
        if tag_id not in tag_ids_in_links:
            warnings.append(f"unused tag {tag_id} ({tags[tag_id][1]})")

    print(f"Validated: {args.sql}")
    for table, count in counts.items():
        print(f"  {table:<22} {count:>6} rows")
    if warnings:
        print(f"\nWARNINGS: {len(warnings)}")
        for warning in warnings:
            print(f"  - {warning}")
    if errors:
        print(f"\nFAILED: {len(errors)} error(s)")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("\nPASSED: no blocking errors found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
