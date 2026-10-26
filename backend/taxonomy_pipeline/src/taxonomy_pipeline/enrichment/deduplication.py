"""Collect overlap evidence without merging source identities or changing producer classifications.

Repeated hierarchy paths are consolidated within their parents. Tag comparison
uses the saturation engine's name keys and lexical scorer; its SemanticMatcher
can be supplied through the shared matching protocol. No comparison report is needed.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict

import numpy as np
from rapidfuzz import fuzz, process

from ..matching.names import match_key, normalize
from ..matching.semantic import SimilarityMatcher as SimilarityMatcher
from .models import (
    BatchValidationReport,
    DeduplicationReport,
    DuplicateCandidate,
    HierarchyGroup,
    InputLocation,
    IntakeRecord,
    SourceTagRef,
    TagProposal,
)


# Sort and deduplicate evidence locations so input iteration order cannot change the report.
def _origins(records: list[IntakeRecord]) -> list[InputLocation]:
    return [
        InputLocation(input_name=name, row_index=index)
        for name, index in sorted({(record.origin.input_name, record.origin.row_index) for record in records})
    ]


# Preserve distinct source tags and all score/reason variants while grouping their placements.
def _collect_tags(records: list[IntakeRecord]) -> list[TagProposal]:
    groups: dict[tuple[str, str], list[IntakeRecord]] = defaultdict(list)
    for record in records:
        groups[(record.output.source_dataset, record.output.tag_id)].append(record)
    tags = []
    for (dataset, identifier), group in sorted(groups.items()):
        if len({(record.output.tag, record.output.original_classification) for record in group}) != 1:
            raise ValueError("Deduplication requires intake to resolve conflicting source identities first")
        placements = {record.output.model_dump_json(): record.output for record in group}
        first = group[0].output
        tags.append(TagProposal(
            source=SourceTagRef(source_dataset=dataset, tag_id=identifier), tag=first.tag,
            original_classification=first.original_classification,
            placements=[placements[key].model_copy(deep=True) for key in sorted(placements)], origins=_origins(group),
        ))
    return tags


# Consolidate identical hierarchy paths without merging names across different parents.
def _collect_hierarchy(
    records: list[IntakeRecord], *, allow_unresolved_targets: bool = False,
) -> list[HierarchyGroup]:
    groups: dict[tuple[str, ...], list[IntakeRecord]] = defaultdict(list)
    for record in records:
        output = record.output
        path = (output.service, normalize(output.category), normalize(output.speciality))
        groups[path[:2]].append(record)
        groups[path].append(record)
    hierarchy = []
    for path, group in sorted(groups.items()):
        is_category = len(path) == 2
        identifiers = {
            record.target.category_id if is_category else record.target.speciality_id
            for record in group if record.target is not None
        }
        if len(identifiers) != 1 and not allow_unresolved_targets:
            raise ValueError("Inconsistent resolved IDs for one hierarchy path; rerun validation against one taxonomy")
        variants = {
            (record.output.service, record.output.category) if is_category else
            (record.output.service, record.output.category, record.output.speciality) for record in group
        }
        sources = sorted({(record.output.source_dataset, record.output.tag_id) for record in group})
        hierarchy.append(HierarchyGroup(
            level="category" if is_category else "speciality", path=list(path),
            variants=[list(variant) for variant in sorted(variants)],
            existing_id=identifiers.pop() if len(identifiers) == 1 else None,
            members=[SourceTagRef(source_dataset=dataset, tag_id=identifier) for dataset, identifier in sources],
            origins=_origins(group),
        ))
    return hierarchy


# Retrieve finite cosine similarities, reporting unavailable or failed semantic checks explicitly.
def _semantic_scores(
    names: list[str], matcher: SimilarityMatcher | None,
) -> tuple[np.ndarray | None, dict[str, str]]:
    if len(names) < 2:
        return None, {}
    if matcher is None:
        return None, {"semantic": "No semantic matcher supplied; only name and lexical checks ran"}
    try:
        scores = np.asarray(matcher.similarity(names, names), dtype=float)
        if scores.shape != (len(names), len(names)) or not np.isfinite(scores).all():
            raise ValueError("Expected a finite square similarity matrix")
        if np.any(scores < -1.000001) or np.any(scores > 1.000001):
            raise ValueError("Cosine similarities must be between -1 and 1")
        if not np.allclose(scores, scores.T, atol=1e-6):
            raise ValueError("Expected symmetric similarities when comparing the same names")
        return np.clip(scores, -1, 1), {}
    except Exception as exc:
        return None, {"semantic": f"Semantic matcher failed: {type(exc).__name__}: {exc}"}


# Record every matching pair once, retaining weaker matches as evidence without creating transitive groups.
def _find_candidates(
    tags: list[TagProposal], lexical_threshold: float, semantic_threshold: float,
    semantic_matcher: SimilarityMatcher | None,
) -> tuple[list[DuplicateCandidate], dict[str, str]]:
    names = [normalize(tag.tag) for tag in tags]
    keys = [match_key(tag.tag) for tag in tags]
    scores = process.cdist(keys, keys, scorer=fuzz.ratio, dtype=np.float64) / 100
    semantic, skipped = _semantic_scores(names, semantic_matcher)
    paths = [
        {(row.service, normalize(row.category), normalize(row.speciality)) for row in tag.placements}
        for tag in tags
    ]
    candidates = []
    for left in range(len(tags)):
        for right in range(left + 1, len(tags)):
            lexical = float(scores[left, right])
            meaning = float(semantic[left, right]) if semantic is not None else None
            kinds = []
            if names[left] and names[left] == names[right]:
                kinds.append("normalized_name")
            elif keys[left] and keys[left] == keys[right]:
                kinds.append("match_key")
            if keys[left] and keys[right] and lexical >= lexical_threshold:
                kinds.append("lexical")
            if meaning is not None and meaning >= semantic_threshold:
                kinds.append("semantic")
            if not kinds:
                continue
            shared = paths[left] & paths[right]
            reason = f"Candidate from {', '.join(kinds)} checks. "
            reason += "Shares a proposed placement. " if shared else "No shared proposed placement. "
            reason += "Confirm equivalence using source context; scores do not approve a merge."
            candidates.append(DuplicateCandidate(
                left=tags[left].source, right=tags[right].source, reason=reason,
                similarity=max(lexical, meaning or 0), match_types=kinds,
                lexical_score=lexical, semantic_score=meaning, shared_paths=[list(path) for path in sorted(shared)],
            ))
    return candidates, skipped


# Build deduplication evidence from eligible intake rows while retaining omissions and check coverage.
def deduplicate_outputs(
    validation: BatchValidationReport, *, lexical_threshold: float = 0.9, semantic_threshold: float = 0.7,
    semantic_matcher: SimilarityMatcher | None = None,
) -> DeduplicationReport:
    """Consolidate paths and propose tag pairs; no source tags are automatically merged.

    Both thresholds use 0..1. `complete` requires readable input files and every
    matching check to run; it does not mean the proposals are approved or resolved.
    """
    for name, value in (("lexical_threshold", lexical_threshold), ("semantic_threshold", semantic_threshold)):
        if (
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not 0 <= value <= 1
        ):
            raise ValueError(f"{name} must be a finite number between 0 and 1")
    records = []
    omitted: Counter[str] = Counter()
    for record in validation.records:
        if (
            record.status in {"valid", "duplicate"} and record.output is not None
            and record.output.category is not None and record.target is not None
        ):
            records.append(record)
        else:
            omitted[record.status] += 1
    tags = _collect_tags(records)
    hierarchy = _collect_hierarchy(records)
    # Screen complete proposals held by intake as well; they still remain omitted from resolution.
    screening_hierarchy = _collect_hierarchy([
        record for record in validation.records
        if record.output is not None and record.output.service is not None
    ], allow_unresolved_targets=True)
    candidates, skipped = _find_candidates(tags, lexical_threshold, semantic_threshold, semantic_matcher)
    counts = {
        "received_records": len(validation.records), "included_records": len(records),
        "omitted_records": sum(omitted.values()), "source_tags": len(tags),
        "unique_placements": len({
            (row.output.source_dataset, row.output.tag_id, row.output.service,
             normalize(row.output.category), normalize(row.output.speciality)) for row in records
        }),
        "candidate_pairs": len(candidates),
    }
    for level in ("category", "speciality"):
        counts[f"reused_{level}_nodes"] = sum(
            node.level == level and node.existing_id is not None for node in hierarchy
        )
        counts[f"new_{level}_nodes"] = sum(node.level == level and node.existing_id is None for node in hierarchy)
    return DeduplicationReport(
        taxonomy=validation.taxonomy, input_complete=validation.complete, complete=validation.complete and not skipped,
        skipped_checks=skipped, thresholds={"lexical": lexical_threshold, "semantic": semantic_threshold},
        counts=counts, omitted_counts=dict(sorted(omitted.items())),
        tags=tags, hierarchy=hierarchy, candidates=candidates,
        screening_hierarchy=screening_hierarchy,
    )
