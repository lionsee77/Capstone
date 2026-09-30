"""Validate AI taxonomy proposals against the generated SkillsFuture hierarchy."""
from __future__ import annotations

import argparse
import csv
import json
from difflib import SequenceMatcher
from pathlib import Path

from validate_skillsfuture_taxonomy import load_rows

ROOT = Path(__file__).parent
VALID_ACTIONS = {"assign_existing", "propose_new_speciality", "propose_new_category"}


def normalise(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--proposals", type=Path, default=ROOT / "classification_proposals.json")
    parser.add_argument("--sql", type=Path, default=ROOT / "skillsfuture_taxonomy.sql")
    parser.add_argument("--output", type=Path, default=ROOT / "classification_proposals_validated.json")
    args = parser.parse_args()

    proposals = json.loads(args.proposals.read_text(encoding="utf-8"))
    data = load_rows(args.sql)
    category_by_name = {normalise(row[1]): row[0] for row in data["sf_categories"]}
    speciality_by_name = {(row[2], normalise(row[1])): row[0] for row in data["sf_specialities"]}
    speciality_names = [row[1] for row in data["sf_specialities"]]
    tags = {row[0]: row[1] for row in data["sf_tags"]}
    seen_tags: set[str] = set()
    checked = []

    for proposal in proposals:
        errors, warnings = [], []
        tag_id = str(proposal.get("tag_id", "")); tag = str(proposal.get("tag", ""))
        action = proposal.get("action"); category = str(proposal.get("category", "")); speciality = str(proposal.get("speciality", ""))
        if tag_id in seen_tags: errors.append("duplicate tag_id proposal")
        seen_tags.add(tag_id)
        if tag_id not in tags: errors.append("tag_id does not exist in sf_tags")
        elif normalise(tag) != normalise(tags[tag_id]): errors.append(f"tag does not match sf_tags value: {tags[tag_id]!r}")
        if action not in VALID_ACTIONS: errors.append(f"invalid action: {action!r}")
        try:
            confidence = float(proposal.get("confidence"))
            if not 0 <= confidence <= 1: errors.append("confidence must be between 0 and 1")
        except (TypeError, ValueError): errors.append("confidence is not numeric")

        category_id = category_by_name.get(normalise(category))
        exact_speciality = (category_id, normalise(speciality)) in speciality_by_name
        if action == "assign_existing":
            if category_id is None: errors.append("assign_existing category does not exist")
            elif not exact_speciality: errors.append("assign_existing speciality does not exist under this category")
        elif action == "propose_new_speciality":
            if category_id is None: errors.append("new speciality must use an existing category")
            elif exact_speciality: errors.append("proposed speciality already exists under this category")
            else:
                similar = max((SequenceMatcher(None, normalise(speciality), normalise(name)).ratio() for name in speciality_names), default=0)
                if similar >= 0.82: warnings.append("new speciality is very similar to an existing speciality")
        elif action == "propose_new_category":
            if category_id is not None: errors.append("proposed new category already exists")
            if not speciality.strip(): errors.append("new category proposal must include a speciality")

        checked.append({**proposal, "review_status": "needs_review" if errors else "pending", "validation_errors": errors, "validation_warnings": warnings})

    args.output.write_text(json.dumps(checked, indent=2, ensure_ascii=False), encoding="utf-8")
    csv_path = args.output.with_suffix(".csv")
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        fields = ["tag", "tag_id", "action", "category", "speciality", "confidence", "reason", "review_status", "validation_errors", "validation_warnings"]
        writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader()
        for row in checked: writer.writerow({**row, "validation_errors": "; ".join(row["validation_errors"]), "validation_warnings": "; ".join(row["validation_warnings"])})
    invalid = sum(row["review_status"] == "needs_review" for row in checked); warning_count = sum(bool(row["validation_warnings"]) for row in checked)
    print(f"Validated {len(checked)} proposals; pending={len(checked)-invalid}, needs_review={invalid}, warnings={warning_count}")
    print(f"Wrote {args.output} and {csv_path}")
    return 1 if invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())
