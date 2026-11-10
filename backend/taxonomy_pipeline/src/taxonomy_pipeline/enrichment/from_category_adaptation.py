"""Turn category_adaptation's internal placements into the classification
output contract (../../../CLASSIFICATION_OUTPUT_CONTRACT.md) - the only thing
that should leave Case A for Bryan's side. Internal fields (match_type, raw
scores, ...) never appear here; only the nine contract fields, plus the optional
`needs_review` on review candidates.
"""
from __future__ import annotations

from typing import Any

from .category_adaptation import (
    CATEGORY_CONTENT_BASIS,
    CONTENT_BASIS,
    INVALID_CATEGORY_NAME,
    NEW_CATEGORY,
    NEW_SPECIALITY,
    NO_SOURCE_CATEGORY,
    RESOLVED_CATEGORY_ACTIONS,
    REVIEW_CANDIDATE_BASIS,
)
from .models import ClassificationOutput

# A from-scratch proposal (new category, or a new speciality under an
# existing category) has no baseline evidence of its own to score. Rather
# than one flat number - which would make a new speciality under a perfectly
# matched category look identical to one under a shaky category match - its
# confidence is scaled by whatever *is* verified elsewhere in the same
# placement: never below the floor, never as high as a real match (capped at
# the ceiling) since it's still unverified either way.
_NEW_PROPOSAL_FLOOR = 0.3
_NEW_PROPOSAL_CEILING = 0.6


def _confidence(score: float | None, semantic_score: float | None) -> float | None:
    """One 0-1 confidence from whichever evidence exists - semantic_score is
    already 0-1 and more meaningful than raw lexical percent, so it wins when
    both are present; score (0-100, lexical/exact) is the fallback."""
    if semantic_score is not None:
        return round(float(semantic_score), 4)
    if score is not None:
        return round(float(score) / 100, 4)
    return None


def _new_proposal_confidence(known_confidence: float | None) -> float:
    """Confidence for a from-scratch proposal. `known_confidence` is the
    *other* level's real confidence, when that level is an actual verified
    match (e.g. the category matched, only the speciality is new) - None
    when there's nothing verified anywhere in this placement (both category
    and speciality are new), which falls back to the floor."""
    if known_confidence is None:
        return _NEW_PROPOSAL_FLOOR
    return round(_NEW_PROPOSAL_FLOOR + (_NEW_PROPOSAL_CEILING - _NEW_PROPOSAL_FLOOR) * known_confidence, 4)


def _category_reason(placement: dict[str, Any]) -> str:
    action = placement["action"]
    name = placement.get("source_category_name")
    if action in ("SKIP_DUPLICATE", "SKIP_SEMANTIC_DUPLICATE"):
        return (
            f"Source category '{name}' matches existing category "
            f"'{placement['target_category_name']}' ({placement['category_match_type']} match)."
        )
    if action == NEW_CATEGORY:
        return f"No existing category matches source category '{name}'; proposing it as a new category."
    if action == "REVIEW_AMBIGUOUS":
        lexical, lexical_score = placement.get("closest_baseline_name"), placement.get("closest_baseline_score")
        semantic, semantic_score = (
            placement.get("closest_semantic_baseline_name"), placement.get("closest_semantic_score")
        )
        hints = []
        # Lexical and semantic near-misses often point at different baseline
        # concepts (spelling similarity isn't meaning similarity) - both are
        # worth a reviewer's attention, not just whichever ran first.
        if semantic:
            hints.append(f"closest in meaning to '{semantic}' ({semantic_score})")
        if lexical and lexical != semantic:
            hints.append(f"closest in spelling to '{lexical}' ({lexical_score})")
        if hints:
            return (
                f"Source category '{name}' is {' and '.join(hints)}, below the confidence threshold; "
                "needs manual review."
            )
        return f"Source category '{name}' has no confident match; needs manual review."
    if action == "REVIEW_NEW_PLACEMENT":
        unmatched = placement.get("unmatched_incoming_parents") or []
        existing = placement.get("existing_parents") or []
        detail = ""
        if unmatched and existing:
            detail = f" (source gives it '{', '.join(unmatched)}'; baseline already has '{', '.join(existing)}')"
        return (
            f"Source category '{name}' matches existing category '{placement['target_category_name']}', but "
            f"under a different parent than the source gives it{detail}; needs manual review."
        )
    if action == "REVIEW_OTHER_LEVEL":
        return f"Source category '{name}' matches an existing concept at a different level; needs manual review."
    if action == INVALID_CATEGORY_NAME:
        return f"Source category name '{name}' is blank or a placeholder."
    if action == NO_SOURCE_CATEGORY:
        return (
            f"Tag '{placement['tag_name']}' has no source category or speciality in this dataset; Case A has "
            "nothing to place it against - route to Case B (AI classification)."
        )
    return "Unresolved."


def _evidence_reason(placement: dict[str, Any]) -> str:
    """Why the tag fell back to its source category: what links it there, and what it nearly matched."""
    reason = ""
    count = placement.get("source_speciality_count")
    if count:
        sample = ", ".join(placement.get("source_speciality_sample") or [])
        more = ", ..." if count > len(placement.get("source_speciality_sample") or []) else ""
        reason += (
            f" Linked to {count} source specialit{'y' if count == 1 else 'ies'} in "
            f"'{placement.get('source_category_name')}' ({sample}{more})."
        )
    semantic, lexical = placement.get("closest_semantic_speciality_name"), placement.get("closest_speciality_name")
    if semantic or lexical:
        hints = []
        if semantic:
            hints.append(f"'{semantic}' in meaning ({placement.get('closest_semantic_speciality_score')})")
        if lexical and lexical != semantic:
            hints.append(f"'{lexical}' in spelling ({placement.get('closest_speciality_score')})")
        reason += f" Closest existing speciality: {' and '.join(hints)}, below the confidence threshold."
    return reason + _other_categories_reason(placement)


def _other_categories_reason(placement: dict[str, Any]) -> str:
    """The source categories `one_category_per_tag` set aside, so a reviewer still sees them."""
    others = placement.get("other_source_categories") or []
    if not others:
        return ""
    return (
        f" Also linked to source categor{'y' if len(others) == 1 else 'ies'} {', '.join(repr(o) for o in others)};"
        " placed via the one with the most evidence only."
    )


def _speciality_group_reason(placement: dict[str, Any]) -> str:
    """How grouping, naming and `flag_single_tag_specialities` shaped the new speciality (#132)."""
    reason = ""
    size = placement.get("speciality_group_size")
    if size:
        others = size - 1
        central = placement.get("speciality_group_central_tag")
        if placement.get("speciality_name_source") == "generated":
            named = (f"named '{placement['target_speciality_name']}' by {placement.get('speciality_name_model')} "
                     "from all its tags; check the name before approving")
        else:
            named = f"named after its most central tag, '{central}'"
            if placement.get("speciality_name_fallback"):
                named += f" ({placement['speciality_name_fallback']})"
        if placement.get("speciality_group_leader"):
            sample = ", ".join(repr(name) for name in placement.get("speciality_group_sample") or [])
            more = ", ..." if others > len(placement.get("speciality_group_sample") or []) else ""
            reason += (
                f" Groups {size} new tags close in meaning as one speciality, {named}. This tag is the most "
                f"central; the others include {sample}{more}."
            )
        else:
            reason += (
                f" Grouped with {others} other new tag{'' if others == 1 else 's'} close in meaning "
                f"(semantic {placement.get('speciality_group_score')} to '{central}') under one new speciality, "
                f"{named}."
            )
    if placement.get("single_tag_speciality"):
        reason += (
            " This new speciality would hold only this tag, so it is flagged for review: approve it, or place "
            "the tag under another speciality."
        )
    return reason


def _category_content_output(placement: dict[str, Any], source_dataset: str) -> ClassificationOutput:
    """A tag matched by meaning to an existing speciality inside its matched category (#132)."""
    speciality, category = placement["target_speciality_name"], placement["target_category_name"]
    score = placement["speciality_semantic_score"]
    return ClassificationOutput(
        source_dataset=source_dataset,
        tag_id=str(placement["tag_id"]),
        tag=placement["tag_name"],
        original_classification="classified",
        service=placement["target_service_name"],
        category=category,
        speciality=speciality,
        confidence=round(float(score), 4),
        reason=(
            f"Source category '{placement['source_category_name']}' matches existing category '{category}' "
            f"({placement['category_match_type']} match). Among that category's specialities the tag is closest "
            f"in meaning to existing speciality '{speciality}' (semantic {score}), so it joins it instead of "
            "becoming a new one-tag speciality."
            + _other_categories_reason(placement)
        ),
    )


def _content_output(placement: dict[str, Any], source_dataset: str) -> ClassificationOutput:
    """A tag matched straight to an existing baseline speciality, wherever that speciality sits."""
    speciality, category = placement["target_speciality_name"], placement["target_category_name"]
    return ClassificationOutput(
        source_dataset=source_dataset,
        tag_id=str(placement["tag_id"]),
        tag=placement["tag_name"],
        original_classification="classified",
        service=placement["target_service_name"],
        category=category,
        speciality=speciality,
        confidence=_confidence(placement.get("speciality_score"), placement.get("speciality_semantic_score")),
        reason=(
            f"Tag matches existing speciality '{speciality}' ({placement['speciality_match_type']} match) "
            f"under '{category}'."
        ),
    )


def _review_candidate_output(placement: dict[str, Any], source_dataset: str) -> ClassificationOutput:
    """One of the closest categories offered for a source category that only partly matched.

    Flagged `needs_review`, so merge-preview holds the tag until a reviewer approves
    one candidate path. Confidence is the candidate's own similarity (source category
    name vs candidate category name), so a reviewer can rank the candidates by it;
    `needs_review`, not confidence, is what keeps the tag from being accepted.
    """
    score = placement["candidate_score"]
    similarity = score / 100 if placement["candidate_score_type"] == "lexical" else max(score, 0.0)
    category, speciality = placement["candidate_category_name"], placement["target_speciality_name"]
    return ClassificationOutput(
        source_dataset=source_dataset,
        tag_id=str(placement["tag_id"]),
        tag=placement["tag_name"],
        original_classification="classified",
        service=placement["candidate_service_name"],
        category=category,
        speciality=speciality,
        confidence=round(similarity, 4),
        reason=(
            _category_reason(placement)
            + f" Review candidate {placement['candidate_rank']} of {placement['candidate_count']}: proposing "
            f"'{speciality}' as a new speciality under '{category}' "
            f"({placement['candidate_score_type']} {score}). Approve one candidate path, or exclude/defer the tag."
            + _speciality_group_reason(placement)
            + _evidence_reason(placement)
        ),
        needs_review=True,
    )


def build_classification_outputs(placements: list[dict[str, Any]], source_dataset: str) -> list[ClassificationOutput]:
    """The Case A producer side of the contract: one ClassificationOutput per
    placement `category_adaptation.py` produced.
    """
    records = []
    for placement in placements:
        if placement.get("placement_basis") == CONTENT_BASIS:
            records.append(_content_output(placement, source_dataset))
            continue
        if placement.get("placement_basis") == REVIEW_CANDIDATE_BASIS:
            records.append(_review_candidate_output(placement, source_dataset))
            continue
        if placement.get("placement_basis") == CATEGORY_CONTENT_BASIS:
            records.append(_category_content_output(placement, source_dataset))
            continue
        action = placement["action"]
        reason = _category_reason(placement)
        service = category = speciality = None
        confidence: float | None = None

        if action in RESOLVED_CATEGORY_ACTIONS:
            category_confidence = (
                _new_proposal_confidence(None)  # the category itself has nothing to verify against
                if action == NEW_CATEGORY
                else _confidence(placement.get("category_score"), placement.get("category_semantic_score"))
            )
            service_name = placement.get("target_service_name")
            if placement.get("speciality_action") == NEW_SPECIALITY and service_name is not None:
                service = service_name
                category = placement["target_category_name"]
                speciality = placement["target_speciality_name"]
                # Scale by the category's confidence only when the category is
                # itself a real, verified match - if it's also new, there's
                # nothing to scale from either.
                confidence = _new_proposal_confidence(category_confidence if action != NEW_CATEGORY else None)
                reason += (
                    f" No existing speciality matches the tag; proposing '{speciality}' as a new speciality "
                    f"under '{category}'."
                ) + _speciality_group_reason(placement)
            elif service_name is None:
                # A brand new category with nothing in this run to vote a
                # service from - the contract requires all four or none, so
                # the record stays fully unresolved even though the category
                # itself is known; `reason` still says so for a human.
                reason += " No service could be determined for this placement."
            else:
                reason += " No speciality could be resolved for this placement."
        reason += _evidence_reason(placement)

        # original_classification describes *this tag* in its source dataset,
        # before Case A/B processing - not "is this source generally Case A".
        # A NO_SOURCE_CATEGORY tag was never actually categorized in
        # SkillsFuture's own data, so it's honestly unclassified, even though
        # most of this source's other tags are.
        original_classification = "unclassified" if action == NO_SOURCE_CATEGORY else "classified"

        records.append(
            ClassificationOutput(
                source_dataset=source_dataset,
                tag_id=str(placement["tag_id"]),
                tag=placement["tag_name"],
                original_classification=original_classification,
                service=service,
                category=category,
                speciality=speciality,
                confidence=confidence,
                reason=reason,
                # The contract allows the flag only on a placement. A single-tag
                # flag or a generated name is only ever set on one.
                needs_review=bool(
                    placement.get("single_tag_speciality") or placement.get("speciality_name_source") == "generated"
                ) and category is not None,
            )
        )
    return records
