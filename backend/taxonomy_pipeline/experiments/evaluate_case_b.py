"""Evaluate saved Case B outputs without making additional model calls.

Each output is checked for Case B coverage and passed through the pipeline's own
intake validation (`enrichment.validation.validate_outputs`, the stage
`merge-preview` runs first), so a prompt version is judged by the same rules its
output will meet. On top of that come two Case B-specific checks the pipeline
doesn't make: each record's `action` is compared with the path validation
actually resolved (`assign_existing` with no placement counts as a mismatch),
and a tag answered more than once is reported. The pipeline treats `action` as
evidence only and accepts several placements per tag, but Case B is asked for
exactly one proposal per tag, so both tell prompt versions apart. The evaluator
reports one independent metrics section per named output for manual comparison.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from taxonomy_pipeline.app.scoring import load_datasets
from taxonomy_pipeline.config import load_config
from taxonomy_pipeline.enrichment.models import IntakeRecord
from taxonomy_pipeline.enrichment.routing import case_b_tag_ids, load_saturation
from taxonomy_pipeline.enrichment.validation import validate_outputs
from taxonomy_pipeline.taxonomy.base import TaxonomyDataset

# Statuses validation gives a record that a reviewer has to look at before it can merge.
NEEDS_REVIEW = {"review", "invalid"}


def resolved_action(record: IntakeRecord) -> str | None:
    """The Case B action the resolved path calls for, or None when validation resolved no path."""
    if record.target is None:
        return None
    if record.target.speciality_id is not None:
        return "assign_existing"
    return "propose_new_speciality" if record.target.category_id is not None else "propose_new_category"


def run_classification_validator(proposals_path: Path, baseline: TaxonomyDataset) -> dict[str, Any]:
    """Validate one output against `baseline` and summarise its findings and action mismatches."""
    report = validate_outputs({proposals_path.name: proposals_path.read_text(encoding="utf-8")}, baseline)
    if not report.complete:
        raise RuntimeError(f"{proposals_path} is not a readable JSON array: {report.input_errors}")
    findings: Counter[str] = Counter()
    mismatches: Counter[str] = Counter()
    tag_ids = Counter(
        str(row.raw["tag_id"]) for row in report.records if isinstance(row.raw, dict) and "tag_id" in row.raw
    )
    needing_review = 0
    for record in report.records:
        findings.update(finding.split(":", 1)[0] for finding in record.findings)
        claimed = record.raw.get("action") if isinstance(record.raw, dict) else None
        actual = resolved_action(record)
        if record.status == "unresolved" and claimed == "assign_existing":
            actual = "no placement"  # claims an existing placement but names none
        mismatched = actual is not None and claimed != actual
        if mismatched:
            mismatches[f"{claimed} -> {actual}"] += 1
        flagged = isinstance(record.raw, dict) and record.raw.get("needs_review") is True
        needing_review += record.status in NEEDS_REVIEW or mismatched or flagged
    duplicate_tag_ids = sorted(tag_id for tag_id, count in tag_ids.items() if count > 1)
    problems = report.counts["invalid"] + report.counts["review"] + sum(mismatches.values()) + len(duplicate_tag_ids)
    return {
        "passed": problems == 0,
        "records": len(report.records),
        "counts": report.counts,
        "records_needing_review": needing_review,
        "findings_by_code": dict(sorted(findings.items())),
        "action_mismatch_count": sum(mismatches.values()),
        "action_mismatches": dict(sorted(mismatches.items())),
        # Case B returns exactly one proposal per tag; a repeat means the model answered a tag twice.
        "duplicate_tag_ids": duplicate_tag_ids,
    }


def evaluate_output(
    path: Path, expected_tag_ids: set[str],
) -> dict[str, Any]:
    """Return proposal-validator coverage checks for one output."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    output_tag_ids = {str(item.get("tag_id")) for item in raw if isinstance(item, dict)}
    return {
        "expected_case_b_tags": len(expected_tag_ids),
        "output_records": len(raw),
        "output_tags": len(output_tag_ids),
        "missing_case_b_tags": sorted(expected_tag_ids - output_tag_ids),
        "unexpected_tags": sorted(output_tag_ids - expected_tag_ids),
    }


def load_baseline(config_path: Path, name: str) -> TaxonomyDataset:
    """Load only the baseline dataset from a pipeline config, as `merge-preview` does."""
    config = load_config(config_path)
    config.datasets = [dataset for dataset in config.datasets if dataset.name == name]
    if not config.datasets:
        raise SystemExit(f"{config_path} has no dataset named {name!r}")
    baseline = load_datasets(config)[name]
    if not isinstance(baseline, TaxonomyDataset):
        raise SystemExit(f"dataset {name!r} is not a taxonomy")
    return baseline


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("configs/original_vs_skillsfuture.yaml"),
                        help="pipeline config holding the baseline dataset")
    parser.add_argument("--baseline", default="original", help="baseline dataset name in --config")
    parser.add_argument("--report", type=Path, required=True, help="saturation report used to route Case B")
    parser.add_argument("--incoming", default="skillsfuture")
    parser.add_argument("--output", action="append", required=True, metavar="VERSION=FILE")
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()
    saturation = load_saturation(args.report, args.incoming)
    expected_tag_ids = {str(tag_id) for tag_id in case_b_tag_ids(saturation)}
    baseline = load_baseline(args.config, args.baseline)
    comparison = {}
    for value in args.output:
        version, separator, filename = value.partition("=")
        if not separator or not version or not filename:
            parser.error("--output must use VERSION=FILE")
        output_path = Path(filename)
        comparison[version] = evaluate_output(output_path, expected_tag_ids)
        comparison[version]["classification_validator"] = run_classification_validator(output_path, baseline)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(comparison, indent=2), encoding="utf-8")
    print(json.dumps(comparison, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
