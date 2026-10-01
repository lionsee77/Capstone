"""Build a non-mutating merge preview with independent structural validation.

Resolution decisions are rechecked against the exact baseline before additions
are assembled. Existing defects remain visible; newly introduced defects block
the entire proposed batch. The flat rows feed a CSV rather than a database import.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any

from ..datasets.base import CONCEPTS, TaxonomyDataset
from ..metrics.integrity import IntegrityMetric, normalize
from ..models import MetricResult
from .models import (
    MergePreview,
    PreviewCounts,
    PreviewRow,
    ResolutionReport,
    ValidationFinding,
    ValidationReport,
)
from .resolution import resolve_outputs, taxonomy_snapshot_id


# Serialize identifiers without conflating booleans, integers, strings, or malformed values.
def _key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


# Compare report content independently of collection order while retaining duplicate entries.
def _ordered(value: Any, field: str = "") -> Any:
    if isinstance(value, list):
        if field == "path":
            return value
        return sorted((_ordered(item) for item in value), key=_key)
    if isinstance(value, dict):
        return {key: _ordered(item, key) for key, item in value.items()}
    return value


# Detect duplicate IDs, invalid IDs, missing or ambiguous parents, and duplicate link pairs explicitly.
def _structural_issues(taxonomy: TaxonomyDataset) -> Counter[tuple[str, str]]:
    issues: Counter[tuple[str, str]] = Counter()
    ids = {table: Counter(_key(row.get("id")) for row in getattr(taxonomy, table)) for table in CONCEPTS}
    for table in CONCEPTS:
        for row in getattr(taxonomy, table):
            identifier = row.get("id")
            if not (
                type(identifier) is int and identifier > 0
                or isinstance(identifier, str) and bool(identifier.strip())
            ):
                issues[("invalid_id", f"{table}: invalid ID {_key(identifier)}")] += 1
        for identifier, count in ids[table].items():
            if count > 1:
                issues[("duplicate_id", f"{table}: duplicate ID {identifier}")] += count - 1
    for table, parent_field, parent_table in (
        ("categories", "service_id", "services"), ("specialities", "category_id", "categories"),
        ("speciality_tags", "speciality_id", "specialities"), ("speciality_tags", "tag_id", "tags"),
    ):
        for row in getattr(taxonomy, table):
            parent = _key(row.get(parent_field))
            if ids[parent_table][parent] != 1:
                message = f"{table} {_key(row.get('id'))}: {parent_field}={parent} must identify one {parent_table} row"
                issues[("invalid_endpoint", message)] += 1
    pairs = Counter(_key([row.get("speciality_id"), row.get("tag_id")]) for row in taxonomy.speciality_tags)
    for pair, count in pairs.items():
        if count > 1:
            issues[("duplicate_link", f"Repeated speciality/tag pair {pair}")] += count - 1
    return issues


# Retain metric diagnostics as supporting evidence rather than treating its status as structural proof.
def _integrity(taxonomy: TaxonomyDataset) -> MetricResult:
    try:
        return IntegrityMetric().run({taxonomy.name: taxonomy}, {"dataset": taxonomy.name})
    except Exception as exc:
        return MetricResult(metric_id="integrity", status="error", summary=f"{type(exc).__name__}: {exc}")


# Add only tags with approved links, preserving baseline rows and stable temporary IDs.
def _assemble(resolution: ResolutionReport, baseline: TaxonomyDataset) -> TaxonomyDataset:
    combined = baseline.model_copy(deep=True)
    for node in sorted(resolution.hierarchy, key=lambda node: (node.level != "category", _key(node.id))):
        if node.existing:
            continue
        table, parent = ("categories", "service_id") if node.level == "category" else ("specialities", "category_id")
        getattr(combined, table).append({"id": node.id, "name": node.name, parent: node.parent_id})
    linked = {link.tag_id for link in resolution.links}
    for tag in sorted(resolution.tags, key=lambda tag: _key(tag.id)):
        if not tag.existing and tag.id in linked:
            combined.tags.append({"id": tag.id, "name": tag.name})
    for link in sorted(resolution.links, key=lambda link: (_key(link.speciality_id), _key(link.tag_id))):
        if not link.existing:
            digest = hashlib.sha256(_key([link.speciality_id, link.tag_id]).encode("utf-8")).hexdigest()
            combined.speciality_tags.append({
                "id": f"new:link:{digest}", "speciality_id": link.speciality_id, "tag_id": link.tag_id,
            })
    for table in CONCEPTS:
        getattr(combined, table).sort(key=_key)
    return combined


# Expand source evidence into readable proposed, reused, unapproved, or awaiting-review rows.
def _placement_rows(resolution: ResolutionReport, blocked: bool) -> list[PreviewRow]:
    nodes = {tuple(node.path): node for node in resolution.hierarchy}
    tags = {tag.id: tag for tag in resolution.tags}
    mappings = {(item.source.source_dataset, item.source.tag_id): item.canonical_tag_id for item in resolution.mappings}
    dispositions = {(item.source.source_dataset, item.source.tag_id): item for item in resolution.dispositions}
    links = {(link.speciality_id, link.tag_id): link for link in resolution.links}
    rows = []
    for proposal in resolution.evidence.tags:
        source = proposal.source.source_dataset, proposal.source.tag_id
        disposition = dispositions[source]
        tag = tags.get(mappings.get(source))
        for placement in proposal.placements:
            path = placement.service, normalize(placement.category), normalize(placement.speciality)
            speciality, category = nodes.get(path), nodes.get(path[:2])
            link = links.get((speciality.id, tag.id)) if speciality and tag else None
            unapproved = {
                (item.service, normalize(item.category), normalize(item.speciality))
                for item in disposition.unapproved_paths
            }
            approved = disposition.status == "accepted" and path not in unapproved and link is not None
            status = disposition.status if disposition.status != "accepted" else "unapproved"
            if approved:
                status = "blocked" if blocked else "reused" if link.existing else "proposed"
            row = PreviewRow(
                status=status, **placement.model_dump(exclude={"reason"}), reason=placement.reason,
                canonical_tag_id=tag.id if tag else None, canonical_tag=tag.name if tag else "",
                review_reason=disposition.reason,
            )
            if approved:
                row.category_id, row.speciality_id = category.id, speciality.id
                row.category_change = "existing" if category.existing else "new"
                row.speciality_change = "existing" if speciality.existing else "new"
                row.tag_change = "existing" if tag.existing else "new"
                row.link_change = "existing" if link.existing else "new"
            rows.append(row)
    return sorted(rows, key=lambda row: _key(row.model_dump(mode="json")))


# Construct and check a reviewed snapshot while separating prior defects from newly introduced errors.
def build_preview(resolution: ResolutionReport, baseline: TaxonomyDataset) -> MergePreview:
    """Build a dry run; no input mutation or database writes occur.

    A changed baseline raises ValueError. Modified resolution artifacts or new
    structural errors block the batch and return the original baseline as the
    preview taxonomy, with proposed rows marked blocked. Omitted upstream records
    remain aggregate counts because deduplication does not retain their raw rows.
    """
    if taxonomy_snapshot_id(baseline) != resolution.baseline_snapshot_id:
        raise ValueError("Baseline snapshot changed; rerun resolution before building a preview")
    expected = resolve_outputs(resolution.evidence, baseline, resolution.decisions)
    findings = []
    fields = {"hierarchy", "tags", "links", "mappings", "dispositions"}
    if _ordered(expected.model_dump(include=fields)) != _ordered(resolution.model_dump(include=fields)):
        findings.append(ValidationFinding(
            code="introduced.resolution_mismatch",
            message="Resolution artifacts differ from the recorded review decisions",
        ))
    before = _structural_issues(baseline)
    candidate = _assemble(expected, baseline)
    introduced = _structural_issues(candidate) - before
    for prefix, issues in (("baseline", before), ("introduced", introduced)):
        findings.extend(ValidationFinding(code=f"{prefix}.{code}", message=f"{message}; occurrences={count}")
                        for (code, message), count in sorted(issues.items()))
    blocked = any(finding.code.startswith("introduced.") for finding in findings)
    combined = baseline.model_copy(deep=True) if blocked else candidate
    for table in CONCEPTS:
        getattr(combined, table).sort(key=_key)
    baseline_integrity, preview_integrity = _integrity(baseline), _integrity(combined)
    for name, metric in (("baseline", baseline_integrity), ("preview", preview_integrity)):
        if metric.status != "ok":
            findings.append(ValidationFinding(code=f"supporting.{name}_integrity", message=metric.summary))
    findings.extend(ValidationFinding(code=f"skipped.{name}", message=reason)
                    for name, reason in sorted(expected.evidence.skipped_checks.items()))
    rows = _placement_rows(expected, blocked)
    linked = set() if blocked else {link.tag_id for link in expected.links}
    counts = PreviewCounts(
        added_tags=sum(not tag.existing and tag.id in linked for tag in expected.tags),
        reused_tags=sum(tag.existing and tag.id in linked for tag in expected.tags),
        added_links=0 if blocked else sum(not link.existing for link in expected.links),
        reused_links=0 if blocked else sum(link.existing for link in expected.links),
        omitted_records=sum(expected.evidence.omitted_counts.values()),
        pending_sources=expected.counts["pending_sources"], deferred_sources=expected.counts["deferred_sources"],
        excluded_sources=expected.counts["excluded_sources"],
        unapproved_placements=sum(row.status == "unapproved" for row in rows),
        **{f"{action}_{table}": 0 if blocked else sum(
            node.level == level and node.existing == (action == "reused") for node in expected.hierarchy
        ) for action in ("added", "reused")
          for level, table in (("category", "categories"), ("speciality", "specialities"))},
    )
    partial = expected.partial or blocked or baseline_integrity.status != "ok" or preview_integrity.status != "ok"
    summaries = counts.model_dump() | {
        "received_input_records": expected.evidence.counts["received_records"],
        "eligible_input_records": expected.evidence.counts["included_records"],
    }
    csv_rows = [PreviewRow(
        row_type="summary", status="blocked" if blocked else "partial" if partial else "complete",
        note=name, count=count,
    ) for name, count in summaries.items()]
    csv_rows.insert(0, PreviewRow(
        row_type="summary", status="blocked" if blocked else "partial" if partial else "complete",
        note=f"Baseline: {baseline.name}; snapshot: {expected.baseline_snapshot_id}",
    ))
    csv_rows.extend(rows)
    csv_rows.extend(PreviewRow(
        row_type="omission", status=status, count=count,
        note="Upstream records omitted before resolution; inspect validation.json for individual rows",
    ) for status, count in sorted(expected.evidence.omitted_counts.items()))
    csv_rows.extend(PreviewRow(row_type="check", status=finding.code, note=finding.message) for finding in findings)
    unresolved_paths = {
        (row.source_dataset, row.tag_id, row.service, row.category, row.speciality)
        for row in rows if row.status in {"pending", "deferred", "unapproved"}
    }
    return MergePreview(
        taxonomy=combined, baseline_snapshot_id=expected.baseline_snapshot_id,
        validation=ValidationReport(
            complete=not blocked, findings=findings, duplicate_candidates=expected.evidence.candidates,
            unresolved=[placement for tag in expected.evidence.tags for placement in tag.placements if (
                placement.source_dataset, placement.tag_id, placement.service, placement.category, placement.speciality,
            ) in unresolved_paths],
        ),
        mappings=[item.model_copy(deep=True) for item in expected.mappings if item.canonical_tag_id in linked],
        decisions=expected.decisions, counts=counts, partial=partial, blocked=blocked, rows=csv_rows,
        resolution=expected, baseline_integrity=baseline_integrity, preview_integrity=preview_integrity,
    )
