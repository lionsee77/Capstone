"""Validate combined producer exports while legacy handoffs are being migrated.

Adapters are explicit per input. Raw rows are preserved; missing categories and
specialities are proposed additions under their supplied parents. Matching tags and approving merges remain
separate operations. No upstream comparison report or database connection is needed.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Collection, Mapping
from typing import Any

from ..datasets.base import TaxonomyDataset
from ..metrics.integrity import normalize
from .models import (
    BatchValidationReport,
    ClassificationOutput,
    InputLocation,
    IntakeRecord,
    LegacyClassificationOutput,
    PlacementTarget,
)

_CASE_A_FIELDS = {
    "source_dataset", "tag_id", "original_tag", "original_classification",
    "category", "speciality", "confidence", "reason",
}
_CASE_B_FIELDS = {"tag_id", "tag", "action", "category", "speciality", "confidence", "reason"}
_ACTIONS = {"assign_existing", "propose_new_category", "propose_new_speciality"}
_HANDOFF = re.compile(r"\broute\s+to\s+case\s+b\b", re.IGNORECASE)


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


# Map either explicitly selected legacy format into the shared temporary intake shape.
def _adapt(raw: Any, case: str, source_dataset: str | None) -> dict[str, Any]:
    fields = _CASE_A_FIELDS if case == "case_a" else _CASE_B_FIELDS
    if not isinstance(raw, dict) or set(raw) != fields:
        raise ValueError(f"{case} requires exactly these fields: {', '.join(sorted(fields))}")
    mapped = dict(raw)
    if case == "case_a":
        mapped["tag"] = mapped.pop("original_tag")
    else:
        action = mapped.pop("action")
        if not isinstance(action, str) or action not in _ACTIONS:
            raise ValueError("Unsupported Case B action")
        mapped.update(source_dataset=source_dataset, original_classification="unclassified")
    candidate = LegacyClassificationOutput.model_validate(mapped).model_dump()
    candidate["service"] = "Industry" if candidate["category"] is not None else None
    return candidate


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
def _validate_record(
    record: IntakeRecord, taxonomy: TaxonomyDataset, case: str | None,
    source_dataset: str | None, skip_handoffs: bool,
) -> None:
    try:
        if case:
            candidate = _adapt(record.raw, case, source_dataset)
        else:
            candidate = ClassificationOutput.model_validate(record.raw).model_dump()
    except (ValueError, TypeError) as exc:
        record.findings.append(f"invalid_output: {exc}")
        return
    record.candidate = candidate
    if (
        case == "case_a" and skip_handoffs and candidate["category"] is None
        and _HANDOFF.search(candidate["reason"])
    ):
        record.status = "skipped"
        record.findings.append("case_a_handoff: temporarily ignored pending producer correction")
        return
    if candidate["category"] is None:
        record.status = "unresolved"
    else:
        if case:
            record.findings.append("temporary_service: Industry assumed for this legacy placement")
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


# Combine named JSON batches using opt-in legacy adapters and return a complete row accounting.
def validate_outputs(
    inputs: Mapping[str, str | bytes], taxonomy: TaxonomyDataset, *,
    case_a_inputs: Collection[str] = (), case_b_sources: Mapping[str, str] | None = None,
    skip_case_a_handoffs: bool = False,
) -> BatchValidationReport:
    """Merge unique valid placements; raw rows, skips, and review findings remain in the report.

    `complete` means every input parsed as a JSON array, not that all placements
    are valid or approved. Case B source names must be supplied by the caller.
    """
    case_b_sources = case_b_sources or {}
    if (set(case_a_inputs) | set(case_b_sources)) - set(inputs):
        raise ValueError("Adapter configuration refers to unknown inputs")
    if set(case_a_inputs) & set(case_b_sources):
        raise ValueError("An input cannot use both legacy adapters")
    if any(not isinstance(value, str) or not value.strip() for value in case_b_sources.values()):
        raise ValueError("Case B requires a nonblank source_dataset")
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
        case = "case_a" if name in case_a_inputs else "case_b" if name in case_b_sources else None
        for index, raw in enumerate(rows):
            record = IntakeRecord(origin=InputLocation(input_name=name, row_index=index), raw=raw)
            _validate_record(record, taxonomy, case, case_b_sources.get(name), skip_case_a_handoffs)
            records.append(record)
    _flag_conflicts(records)
    _collapse_repeats(records)
    counts = Counter(record.status for record in records)
    return BatchValidationReport(
        taxonomy=taxonomy.name, complete=not errors, input_errors=errors, records=records,
        counts={"received": len(records), **{
            status: counts[status] for status in ("valid", "duplicate", "unresolved", "review", "invalid", "skipped")
        }},
        merged_outputs=[record.output for record in records if record.status == "valid"],
    )
