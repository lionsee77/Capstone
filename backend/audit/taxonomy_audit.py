"""Read-only, deterministic audit and inventory of the Senseigigs taxonomy."""

from __future__ import annotations

import argparse
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable

import mysql.connector
from mysql.connector import Error


TABLES = ("services", "categories", "specialities", "tags", "speciality_tags")
NAMED_TABLES = ("services", "categories", "specialities", "tags")
SECTIONS = (
    "Data integrity",
    "Missing or blank values",
    "Exact duplicates",
    "Formatting",
    "Capitalisation",
    "Abbreviations",
    "Placeholder data",
    "Unlinked records",
    "Status consistency",
    "Timestamp consistency",
    "Metadata review",
)
ABBREVIATIONS = {
    "Mgmt": "Management",
    "Ops": "Operations",
    "DD": "Due Diligence",
    "GTM": "Go-to-Market",
}
PLACEHOLDERS = {
    "arc", "dummy", "eee", "new", "new tag", "placeholder", "sample", "temp", "test",
}
EXPECTED_PARENTS = {
    "categories": ("service_id", "services"),
    "specialities": ("category_id", "categories"),
    "speciality_tags.speciality_id": ("speciality_id", "specialities"),
    "speciality_tags.tag_id": ("tag_id", "tags"),
}


def database_config() -> dict[str, Any]:
    """Return local defaults, overridden by the Docker environment when present."""
    return {
        "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
        "port": int(os.getenv("MYSQL_PORT", "3307")),
        "database": os.getenv("MYSQL_DATABASE", "greygigz_beta"),
        "user": os.getenv("MYSQL_USER", "senseigigs"),
        "password": os.getenv("MYSQL_PASSWORD", "localpassword"),
    }


def connect(config: dict[str, Any] | None = None):
    return mysql.connector.connect(**(config or database_config()))


def fetch_all(connection: Any, table: str) -> list[dict[str, Any]]:
    if table not in TABLES:
        raise ValueError(f"Unexpected taxonomy table: {table}")
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(f"SELECT * FROM `{table}`")
        return cursor.fetchall()
    finally:
        cursor.close()


def inspect_schema(connection: Any) -> dict[str, list[dict[str, Any]]]:
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE,
                   COLUMN_KEY, COLUMN_DEFAULT, EXTRA
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME IN (%s, %s, %s, %s, %s)
            ORDER BY TABLE_NAME, ORDINAL_POSITION
            """,
            TABLES,
        )
        columns = cursor.fetchall()
        cursor.execute(
            """
            SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE,
                   GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) AS COLUMNS
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME IN (%s, %s, %s, %s, %s)
            GROUP BY TABLE_NAME, INDEX_NAME, NON_UNIQUE
            ORDER BY TABLE_NAME, INDEX_NAME
            """,
            TABLES,
        )
        indexes = cursor.fetchall()
        cursor.execute(
            """
            SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME,
                   REFERENCED_COLUMN_NAME, CONSTRAINT_NAME
            FROM information_schema.KEY_COLUMN_USAGE
            WHERE TABLE_SCHEMA = DATABASE()
              AND REFERENCED_TABLE_NAME IS NOT NULL
              AND TABLE_NAME IN (%s, %s, %s, %s, %s)
            ORDER BY TABLE_NAME, COLUMN_NAME
            """,
            TABLES,
        )
        foreign_keys = cursor.fetchall()
        return {"columns": columns, "indexes": indexes, "foreign_keys": foreign_keys}
    finally:
        cursor.close()


def load_database(connection: Any) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    return ({table: fetch_all(connection, table) for table in TABLES}, inspect_schema(connection))


def normalized(value: Any) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    value = re.sub(r"[‐‑‒–—―]", "-", value)
    value = re.sub(r"\s+", " ", value)
    return re.sub(r"[.,;:!?]+$", "", value)


def finding(
    section: str,
    issue: str,
    table: str,
    row: dict[str, Any],
    details: str,
    proposed: str = "",
    severity: str = "Review",
) -> dict[str, Any]:
    return {
        "section": section,
        "issue": issue,
        "table": table,
        "id": row.get("id", ""),
        "original": row.get("name", ""),
        "details": details,
        "proposed": proposed,
        "severity": severity,
    }


def audit(data: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)

    # Names are the human-facing identity of every taxonomy record.
    for table in NAMED_TABLES:
        for row in data[table]:
            name = row.get("name")
            if name is None or not str(name).strip():
                out["Missing or blank values"].append(
                    finding("Missing or blank values", "Missing name", table, row,
                            "name is NULL or blank", severity="Critical")
                )
                continue
            name = str(name)
            issues = []
            if name != name.strip():
                issues.append("leading or trailing whitespace")
            if re.search(r"\s{2,}", name):
                issues.append("repeated whitespace")
            if re.search(r"[‐‑‒–—―]", name):
                issues.append("non-standard Unicode dash")
            if re.search(r"[.,;:!?]+$", name):
                issues.append("trailing punctuation")
            if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", name):
                issues.append("control character")
            if issues:
                proposed = unicodedata.normalize("NFKC", name).strip()
                proposed = re.sub(r"\s+", " ", proposed)
                proposed = re.sub(r"[‐‑‒–—―]", "-", proposed)
                proposed = re.sub(r"[.,;:!?]+$", "", proposed)
                proposed = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", proposed)
                out["Formatting"].append(
                    finding("Formatting", "Inconsistent formatting", table, row,
                            ", ".join(issues), proposed, "High")
                )

    for table in ("services", "categories", "specialities"):
        for row in data[table]:
            name = str(row.get("name") or "")
            first_letter = next((character for character in name if character.isalpha()), "")
            if first_letter and first_letter.islower() and not name.startswith("eCommerce"):
                position = name.index(first_letter)
                proposed = name[:position] + first_letter.upper() + name[position + 1:]
                out["Capitalisation"].append(
                    finding("Capitalisation", "Capitalisation", table, row,
                            "name begins with a lowercase letter", proposed, "High")
                )
    for row in data["tags"]:
        name = str(row.get("name") or "")
        proposed = name.lower()
        if name and name != proposed:
            out["Capitalisation"].append(
                finding("Capitalisation", "Capitalisation", "tags", row,
                        "tags should use the existing lowercase convention", proposed, "High")
            )

    for table in ("categories", "specialities"):
        for row in data[table]:
            name = str(row.get("name") or "")
            hits = [key for key in ABBREVIATIONS if re.search(rf"\b{re.escape(key)}\b", name)]
            if hits:
                proposed = name
                for abbreviation in hits:
                    proposed = re.sub(
                        rf"\b{re.escape(abbreviation)}\b", ABBREVIATIONS[abbreviation], proposed
                    )
                details = ", ".join(
                    f"{key} -> {ABBREVIATIONS[key]}" for key in hits
                )
                out["Abbreviations"].append(
                    finding("Abbreviations", "Known abbreviation", table, row,
                            details, proposed)
                )

    scopes = {
        "services": lambda row: (normalized(row.get("name")),),
        "categories": lambda row: (row.get("service_id"), normalized(row.get("name"))),
        "specialities": lambda row: (row.get("category_id"), normalized(row.get("name"))),
        "tags": lambda row: (normalized(row.get("name")),),
        "speciality_tags": lambda row: (row.get("speciality_id"), row.get("tag_id")),
    }
    for table, key_function in scopes.items():
        groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
        for row in data[table]:
            groups[key_function(row)].append(row)
        for key, rows in groups.items():
            if len(rows) > 1:
                ids = ", ".join(str(row.get("id", "")) for row in rows)
                representative = rows[0]
                out["Exact duplicates"].append(
                    finding("Exact duplicates", "Duplicate group", table, representative,
                            f"normalized key {key}; record IDs {ids}",
                            "Review and retain one canonical record", "High")
                )

    for table in ("categories", "specialities", "tags"):
        for row in data[table]:
            name = normalized(row.get("name"))
            match = re.match(r"^(temp|dummy|sample|placeholder)(\b|\s)", name)
            marker = name if name in PLACEHOLDERS else match.group(1) if match else ""
            if marker:
                out["Placeholder data"].append(
                    finding("Placeholder data", "Possible placeholder", table, row,
                            f"placeholder marker: {marker}",
                            "Confirm, then deactivate or remove")
                )

    ids = {table: {row.get("id") for row in data[table]} for table in TABLES}
    parent_checks = (
        ("categories", "service_id", "services"),
        ("specialities", "category_id", "categories"),
        ("speciality_tags", "speciality_id", "specialities"),
        ("speciality_tags", "tag_id", "tags"),
    )
    for table, column, parent_table in parent_checks:
        for row in data[table]:
            parent_id = row.get(column)
            if parent_id is None:
                out["Data integrity"].append(
                    finding("Data integrity", "Missing parent reference", table, row,
                            f"{column} is NULL", severity="Critical")
                )
            elif parent_id not in ids[parent_table]:
                out["Data integrity"].append(
                    finding("Data integrity", "Invalid parent reference", table, row,
                            f"{column}={parent_id} does not exist in {parent_table}",
                            severity="Critical")
                )

    used_services = {row.get("service_id") for row in data["categories"]}
    used_categories = {row.get("category_id") for row in data["specialities"]}
    used_specialities = {row.get("speciality_id") for row in data["speciality_tags"]}
    used_tags = {row.get("tag_id") for row in data["speciality_tags"]}
    usage_checks = (
        ("services", used_services, "service has no categories"),
        ("categories", used_categories, "category has no specialities"),
        ("specialities", used_specialities, "speciality has no tags"),
        ("tags", used_tags, "tag is unused"),
    )
    for table, used_ids, message in usage_checks:
        for row in data[table]:
            if row.get("id") not in used_ids:
                out["Unlinked records"].append(
                    finding("Unlinked records", "Unlinked record", table, row, message,
                            "Review link, intended leaf status, or deactivate")
                )

    service_by_id = {row.get("id"): row for row in data["services"]}
    category_by_id = {row.get("id"): row for row in data["categories"]}
    for table in ("services", "categories", "specialities"):
        for row in data[table]:
            if str(row.get("status")) not in {"0", "1"}:
                out["Status consistency"].append(
                    finding("Status consistency", "Unexpected status", table, row,
                            f"status={row.get('status')!r}; expected 0 or 1", severity="High")
                )
    for row in data["categories"]:
        parent = service_by_id.get(row.get("service_id"))
        if str(row.get("status")) == "1" and parent and str(parent.get("status")) != "1":
            out["Status consistency"].append(
                finding("Status consistency", "Active child under inactive parent", "categories", row,
                        f"parent service {parent.get('id')} is inactive", severity="High")
            )
    for row in data["specialities"]:
        parent = category_by_id.get(row.get("category_id"))
        if str(row.get("status")) == "1" and parent and str(parent.get("status")) != "1":
            out["Status consistency"].append(
                finding("Status consistency", "Active child under inactive parent", "specialities", row,
                        f"parent category {parent.get('id')} is inactive", severity="High")
            )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for table in NAMED_TABLES:
        for row in data[table]:
            created = row.get("created_at")
            updated = row.get("updated_at")
            if isinstance(created, datetime) and created > now:
                out["Timestamp consistency"].append(
                    finding("Timestamp consistency", "Future timestamp", table, row,
                            f"created_at {created.isoformat()} is in the future")
                )
            if isinstance(updated, datetime) and updated > now:
                out["Timestamp consistency"].append(
                    finding("Timestamp consistency", "Future timestamp", table, row,
                            f"updated_at {updated.isoformat()} is in the future")
                )
            if isinstance(created, datetime) and isinstance(updated, datetime) and updated < created:
                out["Timestamp consistency"].append(
                    finding("Timestamp consistency", "Timestamp order", table, row,
                            f"updated_at {updated.isoformat()} precedes created_at {created.isoformat()}")
                )

    # Metadata is optional in the current schema, so report it as review-only and
    # group records with the same missing-field pattern to avoid an enormous report.
    metadata_columns = {
        "services": ("url", "meta_title", "meta_desc", "meta_keywords", "description"),
        "categories": ("url", "meta_title", "meta_desc", "meta_keywords", "description"),
        "specialities": ("url", "meta_title", "meta_desc", "meta_keywords", "description"),
    }
    for table, columns in metadata_columns.items():
        groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
        for row in data[table]:
            if str(row.get("status")) != "1":
                continue
            missing = tuple(column for column in columns if not str(row.get(column) or "").strip())
            if missing:
                groups[missing].append(row)
        for missing, rows in groups.items():
            sample_ids = ", ".join(str(row.get("id")) for row in rows[:10])
            suffix = "" if len(rows) <= 10 else f", and {len(rows) - 10} more"
            out["Metadata review"].append(
                finding("Metadata review", "Incomplete active-record metadata", table, rows[0],
                        f"{len(rows)} active record(s) missing {', '.join(missing)}; IDs {sample_ids}{suffix}",
                        "Populate if these records have public pages")
            )

    return out


def schema_findings(schema: dict[str, Any]) -> list[dict[str, Any]]:
    findings = []
    indexes = schema.get("indexes", [])
    foreign_keys = schema.get("foreign_keys", [])
    indexed = {(row["TABLE_NAME"], row["COLUMNS"]) for row in indexes}
    declared = {
        (row["TABLE_NAME"], row["COLUMN_NAME"], row["REFERENCED_TABLE_NAME"])
        for row in foreign_keys
    }
    expected = (
        ("categories", "service_id", "services"),
        ("specialities", "category_id", "categories"),
        ("speciality_tags", "speciality_id", "specialities"),
        ("speciality_tags", "tag_id", "tags"),
    )
    for table, column, parent in expected:
        if (table, column, parent) not in declared:
            findings.append({
                "severity": "Review", "table": table, "item": column,
                "details": f"No database foreign key enforces {table}.{column} -> {parent}.id",
            })
        if (table, column) not in indexed and not any(
            indexed_table == table and str(columns).split(",")[0] == column
            for indexed_table, columns in indexed
        ):
            findings.append({
                "severity": "High", "table": table, "item": column,
                "details": "Relationship column is not the leading column of an index",
            })
    unique_pairs = {
        (row["TABLE_NAME"], row["COLUMNS"])
        for row in indexes if int(row["NON_UNIQUE"]) == 0
    }
    if ("speciality_tags", "speciality_id,tag_id") not in unique_pairs:
        findings.append({
            "severity": "Review", "table": "speciality_tags", "item": "speciality_id, tag_id",
            "details": "No unique constraint prevents duplicate speciality-tag links",
        })
    return findings


def escape(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def markdown_table(columns: Iterable[str], rows: Iterable[dict[str, Any]]) -> str:
    columns = tuple(columns)
    rows = list(rows)
    if not rows:
        return "_None._"
    lines = [
        "| " + " | ".join(columns) + " |",
        "|" + "|".join("---" for _ in columns) + "|",
    ]
    lines.extend(
        "| " + " | ".join(escape(row.get(column, "")) for column in columns) + " |"
        for row in rows
    )
    return "\n".join(lines)


def distributions(data: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    categories = Counter(row.get("service_id") for row in data["categories"])
    specialities = Counter(row.get("category_id") for row in data["specialities"])
    tags = Counter(row.get("speciality_id") for row in data["speciality_tags"])
    tag_use = Counter(row.get("tag_id") for row in data["speciality_tags"])
    service_rows = [
        {"Service ID": row.get("id"), "Service": row.get("name"),
         "Status": row.get("status"), "Categories": categories[row.get("id")]}
        for row in data["services"]
    ]
    category_rows = [
        {"Category ID": row.get("id"), "Category": row.get("name"),
         "Service ID": row.get("service_id"), "Status": row.get("status"),
         "Specialities": specialities[row.get("id")]}
        for row in data["categories"]
    ]
    speciality_counts = [tags[row.get("id")] for row in data["specialities"]]
    tag_counts = list(tag_use.values())
    summary_rows = [
        {"Measure": "Tags per speciality", "Minimum": min(speciality_counts, default=0),
         "Average": f"{sum(speciality_counts) / len(data['specialities']):.2f}" if data["specialities"] else "0.00",
         "Maximum": max(speciality_counts, default=0)},
        {"Measure": "Specialities per used tag", "Minimum": min(tag_counts, default=0),
         "Average": f"{sum(tag_counts) / len(tag_counts):.2f}" if tag_counts else "0.00",
         "Maximum": max(tag_counts, default=0)},
    ]
    top_tags = []
    tag_names = {row.get("id"): row.get("name") for row in data["tags"]}
    for tag_id, count in tag_use.most_common(20):
        top_tags.append({"Tag ID": tag_id, "Tag": tag_names.get(tag_id, "<missing>"), "Specialities": count})
    return {"services": service_rows, "categories": category_rows,
            "summary": summary_rows, "top_tags": top_tags}


def build_report(
    data: dict[str, list[dict[str, Any]]],
    schema: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> str:
    results = audit(data)
    schema_issues = schema_findings(schema)
    severity_counts = Counter(
        row["severity"] for section in SECTIONS for row in results.get(section, [])
    )
    distribution = distributions(data)
    generated = datetime.now().astimezone().isoformat(timespec="seconds")
    database = (config or {}).get("database", "current database")
    lines = [
        "# Senseigigs Taxonomy Database Audit",
        "",
        f"Generated: `{generated}`  ",
        f"Database: `{escape(database)}`  ",
        "Mode: **read-only** — no database records were changed.",
        "",
        "## Executive summary",
        "",
        markdown_table(
            ("Severity", "Findings"),
            [{"Severity": severity, "Findings": severity_counts.get(severity, 0)}
             for severity in ("Critical", "High", "Review")],
        ),
        "",
        "> A finding is a record or grouped condition requiring attention; duplicate and metadata findings are grouped to keep the report readable.",
        "",
        "## Source inventory",
        "",
        markdown_table(
            ("Table", "Rows"),
            [{"Table": table, "Rows": len(data[table])} for table in TABLES],
        ),
        "",
        "## Findings by check",
        "",
        markdown_table(
            ("Check", "Findings"),
            [{"Check": section, "Findings": len(results.get(section, []))} for section in SECTIONS],
        ),
        "",
        "## Schema safeguards",
        "",
        markdown_table(("severity", "table", "item", "details"), schema_issues),
        "",
        "## Taxonomy distribution",
        "",
        "### Categories by service",
        "",
        markdown_table(("Service ID", "Service", "Status", "Categories"), distribution["services"]),
        "",
        "### Specialities by category",
        "",
        markdown_table(
            ("Category ID", "Category", "Service ID", "Status", "Specialities"),
            distribution["categories"],
        ),
        "",
        "### Link coverage",
        "",
        markdown_table(("Measure", "Minimum", "Average", "Maximum"), distribution["summary"]),
        "",
        "### Most-used tags",
        "",
        markdown_table(("Tag ID", "Tag", "Specialities"), distribution["top_tags"]),
    ]
    detail_columns = ("severity", "table", "id", "original", "issue", "details", "proposed")
    for section in SECTIONS:
        lines.extend(["", f"## {section}", "", markdown_table(detail_columns, results.get(section, []))])
    lines.extend([
        "",
        "## Interpretation notes",
        "",
        "- Critical findings indicate broken identifiers, relationships, or required names.",
        "- High findings are deterministic inconsistencies with a clear expected convention.",
        "- Review findings need a business decision; they should not be deleted automatically.",
        "- Near-duplicate and semantic-overlap detection remains excluded because it requires domain judgement.",
        "- Missing metadata is reported only for active records and is grouped by missing-field pattern.",
    ])
    return "\n".join(lines) + "\n"


def generate_report(config: dict[str, Any] | None = None) -> str:
    resolved = config or database_config()
    connection = connect(resolved)
    try:
        data, schema = load_database(connection)
    finally:
        connection.close()
    return build_report(data, schema, resolved)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a read-only taxonomy database audit.")
    parser.add_argument("--output", help="Write Markdown to this path instead of stdout")
    args = parser.parse_args(argv)
    config = database_config()
    try:
        report = generate_report(config)
    except (Error, OSError, ValueError) as exc:
        print(f"Could not audit MySQL: {exc}", file=sys.stderr)
        print(
            f"Target: {config['host']}:{config['port']}/{config['database']}. "
            "Start it with: docker compose up -d --build",
            file=sys.stderr,
        )
        return 1
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(report)
        print(f"Wrote {args.output}")
    else:
        print(report, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
