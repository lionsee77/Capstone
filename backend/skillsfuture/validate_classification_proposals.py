"""Validate schema-compliant SkillsFuture classification output."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from validate_skillsfuture_taxonomy import load_rows, parse_value_tuple

ROOT = Path(__file__).parent
ALLOWED_SERVICES = {"Industry", "Function", "Subject", "Others"}
ALLOWED_ACTIONS = {"assign_existing", "propose_new_speciality", "propose_new_category"}


def normalise(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


def original_hierarchy(sql_dir: Path) -> tuple[set[str], set[tuple[str, str]]]:
    categories: dict[str, str] = {}
    specialities: set[tuple[str, str]] = set()
    for table, path in (("categories", sql_dir / "categories.sql"), ("specialities", sql_dir / "specialities.sql")):
        current = False
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("INSERT INTO") and f"`{table}`" in line:
                current = True
                continue
            if not current:
                continue
            values = parse_value_tuple(line)
            if values:
                if table == "categories" and len(values) > 9:
                    categories[values[0]] = values[1]
                elif table == "specialities" and len(values) > 8:
                    specialities.add((categories.get(values[8], values[8]), normalise(values[1])))
            if line.strip().endswith(";"):
                current = False
    return {normalise(name) for name in categories.values()}, {(normalise(category), name) for category, name in specialities}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--proposals", type=Path, default=ROOT / "classification_proposals.json")
    parser.add_argument("--sql", type=Path, default=ROOT / "skillsfuture_taxonomy.sql")
    parser.add_argument("--target-sql-dir", type=Path, default=ROOT.parent / "sql-files-nus", help="Ankit taxonomy SQL directory")
    parser.add_argument(
        "--schema",
        type=Path,
        default=ROOT.parent / "taxonomy_pipeline" / "schemas" / "classification_output.schema.json",
    )
    parser.add_argument("--output", type=Path, default=ROOT / "classification_proposals_validated.json")
    args = parser.parse_args()
    records = json.loads(args.proposals.read_text(encoding="utf-8"))
    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    schema_errors = sorted(Draft202012Validator(schema).iter_errors(records), key=lambda error: list(error.path))
    if schema_errors:
        for error in schema_errors[:20]: print(f"SCHEMA ERROR at {list(error.path)}: {error.message}")
        return 1

    data = load_rows(args.sql)
    category_names, speciality_pairs = original_hierarchy(args.target_sql_dir)
    tags = {row[0]: row[1] for row in data["sf_tags"]}
    seen: set[str] = set(); checked = []
    for record in records:
        errors: list[str] = []; warnings: list[str] = []
        tag_id, tag = record["tag_id"], record["tag"]
        category, speciality, service = record["category"], record["speciality"], record["service"]
        action = record["action"]
        if tag_id in seen: errors.append("duplicate tag_id")
        seen.add(tag_id)
        if tag_id not in tags: errors.append("tag_id does not exist in sf_tags")
        elif normalise(tag) != normalise(tags[tag_id]): errors.append(f"tag does not match sf_tags value: {tags[tag_id]!r}")
        if service is not None and service not in ALLOWED_SERVICES: errors.append(f"invalid service: {service!r}")
        if action not in ALLOWED_ACTIONS: errors.append(f"invalid action: {action!r}")
        if category is None or speciality is None:
            if category is not None or speciality is not None or record["confidence"] is not None:
                errors.append("unresolved records must have null category, speciality, and confidence")
            if action == "assign_existing":
                errors.append("assign_existing cannot have an unresolved classification path")
        else:
            category_exists = normalise(category) in category_names
            speciality_exists = (normalise(category), normalise(speciality)) in speciality_pairs
            if action == "assign_existing":
                if not category_exists: errors.append("assign_existing category does not exist in Ankit's taxonomy")
                elif not speciality_exists: errors.append("assign_existing speciality does not exist under this category")
            elif action == "propose_new_speciality":
                if not category_exists: errors.append("new speciality must use an existing Ankit category")
                elif speciality_exists: errors.append("proposed speciality already exists under this category")
            elif action == "propose_new_category":
                if category_exists: errors.append("proposed new category already exists in Ankit's taxonomy")
        checked.append({**record, "review_status": "needs_review" if errors else "pending", "validation_errors": errors, "validation_warnings": warnings})

    args.output.write_text(json.dumps(checked, indent=2, ensure_ascii=False), encoding="utf-8")
    csv_path = args.output.with_suffix(".csv")
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        fields = [
            "action", "source_dataset", "tag_id", "tag", "original_classification", "service", "category",
            "speciality", "confidence", "reason", "needs_review", "saturation_run_id", "review_status",
            "validation_errors", "validation_warnings",
        ]
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in checked: writer.writerow({**row, "validation_errors": "; ".join(row["validation_errors"]), "validation_warnings": "; ".join(row["validation_warnings"])})
    invalid = sum(row["review_status"] == "needs_review" for row in checked); warning_count = sum(bool(row["validation_warnings"]) for row in checked)
    print(f"Validated {len(checked)} classifications; pending={len(checked)-invalid}, needs_review={invalid}, warnings={warning_count}")
    print(f"Wrote {args.output} and {csv_path}")
    return 1 if invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())
