"""Evaluate saved Case B outputs without making additional model calls.

Each output is checked for Case B coverage and passed through the existing
classification validator. The evaluator reports one independent metrics
section per named output for manual comparison.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from taxonomy_pipeline.enrichment.routing import case_b_tag_ids, load_saturation


def run_classification_validator(
    proposals_path: Path, skillsfuture_sql: Path, target_sql_dir: Path,
) -> dict[str, Any]:
    """Run the original proposal validator and summarize its findings."""
    validator = Path(__file__).parents[2] / "skillsfuture" / "validate_classification_proposals.py"
    if not validator.is_file():
        raise FileNotFoundError(
            f"Classification validator is missing: {validator}. "
            "Restore backend/skillsfuture/validate_classification_proposals.py before evaluating."
        )
    with tempfile.TemporaryDirectory(prefix="case-b-validation-") as directory:
        validated_path = Path(directory) / "validated.json"
        result = subprocess.run(
            [
                sys.executable, str(validator), "--proposals", str(proposals_path), "--sql", str(skillsfuture_sql),
                "--target-sql-dir", str(target_sql_dir), "--output", str(validated_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if not validated_path.is_file():
            raise RuntimeError(
                "Classification validator did not produce validated output; "
                f"exit code was {result.returncode}: {(result.stdout + result.stderr).strip()}"
            )
        records = json.loads(validated_path.read_text(encoding="utf-8"))
        if not isinstance(records, list):
            raise RuntimeError("Classification validator produced a non-list validated output")
    errors: dict[str, int] = {}
    warnings: dict[str, int] = {}
    for record in records:
        for error in record.get("validation_errors", []):
            errors[error] = errors.get(error, 0) + 1
        for warning in record.get("validation_warnings", []):
            warnings[warning] = warnings.get(warning, 0) + 1
    return {
        "passed": result.returncode == 0,
        "exit_code": result.returncode,
        "records": len(records),
        "records_needing_review": sum(record.get("review_status") == "needs_review" for record in records),
        "error_count": sum(errors.values()),
        "errors_by_message": dict(sorted(errors.items())),
        "warning_count": sum(warnings.values()),
        "warnings_by_message": dict(sorted(warnings.items())),
        "output": (result.stdout + result.stderr).strip(),
    }


def evaluate_output(
    path: Path, expected_tag_ids: set[str],
) -> dict[str, Any]:
    """Return proposal-validator coverage checks for one output."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    output_tag_ids = {str(item.get("tag_id")) for item in raw if isinstance(item, dict)}
    return {
        "expected_case_b_tags": len(expected_tag_ids),
        "output_tags": len(output_tag_ids),
        "missing_case_b_tags": sorted(expected_tag_ids - output_tag_ids),
        "unexpected_tags": sorted(output_tag_ids - expected_tag_ids),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True, help="Ankit SQL directory for proposal validation")
    parser.add_argument("--report", type=Path, required=True, help="saturation report used to route Case B")
    parser.add_argument("--incoming", default="skillsfuture")
    parser.add_argument(
        "--skillsfuture-sql", type=Path, required=True, help="SkillsFuture SQL used by proposal validation",
    )
    parser.add_argument("--output", action="append", required=True, metavar="VERSION=FILE")
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()
    saturation = load_saturation(args.report, args.incoming)
    expected_tag_ids = {str(tag_id) for tag_id in case_b_tag_ids(saturation)}
    comparison = {}
    for value in args.output:
        version, separator, filename = value.partition("=")
        if not separator or not version or not filename:
            parser.error("--output must use VERSION=FILE")
        output_path = Path(filename)
        comparison[version] = evaluate_output(output_path, expected_tag_ids)
        comparison[version]["classification_validator"] = run_classification_validator(
            output_path, args.skillsfuture_sql, args.baseline,
        )
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(comparison, indent=2), encoding="utf-8")
    print(json.dumps(comparison, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
