"""Validate combined producer exports against the classification contract.

Every input follows the nine-field `ClassificationOutput` contract. Raw rows are
preserved; missing categories and specialities are proposed additions under
their supplied parents. Matching tags and approving merges remain separate
operations. No upstream comparison report or database connection is needed.
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from collections.abc import Mapping
from typing import Any

from ..matching.names import normalize
from ..taxonomy.base import TaxonomyDataset
from .models import (
    BatchValidationReport,
    ClassificationOutput,
    InputLocation,
    IntakeRecord,
    PlacementTarget,
)


# Reject ambiguous JSON objects instead of silently keeping the last duplicate key.
def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Repeated JSON key: {key}")
        result[key] = value
    return result


# Reject non-JSON numeric constants and values that overflow a finite float.
def _finite_number(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("JSON numbers must be finite")
    return number


# Resolve one existing node only when its name and parent identify it unambiguously.
def _existing_node(
    rows: list[dict[str, Any]], name: str, parent: str | None = None, parent_id: int | None = None,
) -> dict[str, Any] | None:
    matches = [
        row for row in rows
        if isinstance(row.get("name"), str) and normalize(row["name"]) == normalize(name)
        and (parent is None or row.get(parent) == parent_id)
    ]
    if not matches:
        return None
    if len(matches) > 1:
        raise ValueError("ambiguous_path: multiple nodes have the same name under this parent")
    row = matches[0]
    identifier = row.get("id")
    if type(identifier) is not int or identifier <= 0 or sum(r.get("id") == identifier for r in rows) != 1:
        raise ValueError("invalid_taxonomy_path: IDs must be positive and unique within each tier")
    if parent and (type(row.get(parent)) is not int or row[parent] <= 0):
        raise ValueError("invalid_taxonomy_path: parent IDs must be positive integers")
    return row


# Reuse nodes within their supplied parents and mark missing child nodes as proposed additions.
def resolve_placement_path(candidate: dict[str, Any], taxonomy: TaxonomyDataset) -> PlacementTarget:
    """Resolve parent-scoped IDs for intake and revalidation before reviewed consolidation."""
    service = _existing_node(taxonomy.services, candidate["service"])
    if service is None:
        raise ValueError("missing_service: the taxonomy must contain the supplied service")
    category = _existing_node(taxonomy.categories, candidate["category"], "service_id", service["id"])
    speciality = None if category is None else _existing_node(
        taxonomy.specialities, candidate["speciality"], "category_id", category["id"],
    )
    return PlacementTarget(
        service_id=service["id"], category_id=category["id"] if category else None,
        speciality_id=speciality["id"] if speciality else None,
    )


# Validate one row while retaining original data and distinguishing reused nodes from proposed additions.
def _validate_record(record: IntakeRecord, taxonomy: TaxonomyDataset) -> None:
    try:
        candidate = ClassificationOutput.model_validate(record.raw).model_dump()
    except (ValueError, TypeError) as exc:
        record.findings.append(f"invalid_output: {exc}")
        return
    record.candidate = candidate
    if candidate["category"] is None:
        record.status = "unresolved"
    else:
        try:
            record.target = resolve_placement_path(candidate, taxonomy)
        except ValueError as exc:
            record.status = "review"
            record.findings.append(str(exc))
        else:
            record.status = "valid"
            if record.target.category_id is None:
                record.findings.append("new_category: proposed under the supplied service")
            if record.target.speciality_id is None:
                record.findings.append("new_speciality: proposed under the supplied category")
    record.output = ClassificationOutput.model_validate(candidate)


# Hold inconsistent source identities before collapsing any repeated placements.
def _flag_conflicts(records: list[IntakeRecord]) -> None:
    groups: dict[tuple[str, str], list[IntakeRecord]] = defaultdict(list)
    for record in records:
        if record.candidate is not None and record.status != "skipped":
            groups[(record.candidate["source_dataset"], record.candidate["tag_id"])].append(record)
    for group in groups.values():
        for field in ("tag", "original_classification"):
            if len({record.candidate[field] for record in group}) > 1:
                for record in group:
                    record.status = "review"
                    record.findings.append(f"conflicting_{field}: source identity has inconsistent {field}")


# Keep one eligible source placement while preserving all repeated input rows as evidence.
def _collapse_repeats(records: list[IntakeRecord]) -> None:
    seen: dict[tuple[Any, ...], InputLocation] = {}
    for record in records:
        if record.status not in {"valid", "unresolved"}:
            continue
        output = record.output
        if record.status == "valid":
            key = (
                "valid", output.source_dataset, output.tag_id, output.service,
                normalize(output.category), normalize(output.speciality),
            )
        else:
            key = ("unresolved", output.model_dump_json())
        if key in seen:
            record.status = "duplicate"
            record.duplicate_of = seen[key]
            record.findings.append("repeated_output: see duplicate_of for retained row")
        else:
            seen[key] = record.origin


# Combine named JSON batches and return a complete row accounting.
def validate_outputs(inputs: Mapping[str, str | bytes], taxonomy: TaxonomyDataset) -> BatchValidationReport:
    """Merge unique valid placements; raw rows and review findings remain in the report.

    `complete` means every input parsed as a JSON array, not that all placements
    are valid or approved. The `skipped` count stays in the report shape for
    merge runs saved while the legacy adapters existed; nothing produces it now.
    """
    records: list[IntakeRecord] = []
    errors: dict[str, str] = {}
    for name, payload in inputs.items():
        try:
            rows = json.loads(
                payload, object_pairs_hook=_unique_object, parse_float=_finite_number, parse_constant=_finite_number,
            )
            if not isinstance(rows, list):
                raise ValueError("Expected a JSON array")
        except (ValueError, TypeError) as exc:
            errors[name] = str(exc)
            continue
        for index, raw in enumerate(rows):
            record = IntakeRecord(origin=InputLocation(input_name=name, row_index=index), raw=raw)
            _validate_record(record, taxonomy)
            records.append(record)
    _flag_conflicts(records)
    _collapse_repeats(records)
    counts = Counter(record.status for record in records)
    total = len(records)
    valid = counts["valid"]
    metrics = {
        "total_records": total,
        "valid_records": valid,
        "review_records": counts["review"],
        "unresolved_records": counts["unresolved"],
        "duplicate_records": counts["duplicate"],
        "invalid_records": counts["invalid"],
        "skipped_records": counts["skipped"],
        "valid_rate": valid / total if total else 0.0,
        "review_rate": counts["review"] / total if total else 0.0,
    }
    return BatchValidationReport(
        taxonomy=taxonomy.name, complete=not errors, input_errors=errors, records=records,
        counts={"received": len(records), **{
            status: counts[status] for status in ("valid", "duplicate", "unresolved", "review", "invalid", "skipped")
        }},
        metrics=metrics,
        merged_outputs=[record.output for record in records if record.status == "valid"],
    )
