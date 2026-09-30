"""Turn category_adaptation's internal placements into the classification
output contract (../../../CLASSIFICATION_OUTPUT_CONTRACT.md) - the only thing
that should leave Case A for Bryan's side. Internal fields (match_type, raw
scores, needs_review, ...) never appear here; only the eight contract fields.
"""
from __future__ import annotations

from typing import Any

from ..metrics.category_adaptation import INVALID_CATEGORY_NAME, NEW_CATEGORY, NEW_SPECIALITY, NO_SOURCE_CATEGORY
from .models import ClassificationOutput

# A resolved category (matched or newly proposed) still needs one of these
# speciality outcomes before the whole placement can be reported - anything
# else (REVIEW_AMBIGUOUS, or no speciality outcome at all) leaves the record
# unresolved, per the contract's all-or-null rule on category/speciality/confidence.
_RESOLVED_SPECIALITY_ACTIONS = {"SKIP_DUPLICATE", "SKIP_SEMANTIC_DUPLICATE", NEW_SPECIALITY}
# A from-scratch proposal (new category, or a new speciality under an
# existing category) has no baseline evidence to score - a fixed, clearly
# documented conservative confidence rather than a fabricated precise one.
_NEW_PROPOSAL_CONFIDENCE = 0.4


def _confidence(score: float | None, semantic_score: float | None) -> float | None:
    """One 0-1 confidence from whichever evidence exists - semantic_score is
    already 0-1 and more meaningful than raw lexical percent, so it wins when
    both are present; score (0-100, lexical/exact) is the fallback."""
    if semantic_score is not None:
        return round(float(semantic_score), 4)
    if score is not None:
        return round(float(score) / 100, 4)
    return None


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
        closest, score = placement.get("closest_baseline_name"), placement.get("closest_baseline_score")
        if closest:
            return (
                f"Source category '{name}' is closest to existing '{closest}' ({score}), below the "
                "confidence threshold; needs manual review."
            )
        return f"Source category '{name}' has no confident match; needs manual review."
    if action == "REVIEW_NEW_PLACEMENT":
        return (
            f"Source category '{name}' matches existing category '{placement['target_category_name']}', but "
            "under a different parent than the source gives it; needs manual review."
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


def build_classification_outputs(placements: list[dict[str, Any]], source_dataset: str) -> list[ClassificationOutput]:
    """The Case A producer side of the contract: one ClassificationOutput per
    placement `category_adaptation.py` produced.
    """
    records = []
    for placement in placements:
        action = placement["action"]
        reason = _category_reason(placement)
        category = speciality = None
        confidence: float | None = None

        if action in ("SKIP_DUPLICATE", "SKIP_SEMANTIC_DUPLICATE", NEW_CATEGORY):
            category_confidence = (
                _NEW_PROPOSAL_CONFIDENCE
                if action == NEW_CATEGORY
                else _confidence(placement.get("category_score"), placement.get("category_semantic_score"))
            )
            speciality_action = placement.get("speciality_action")
            if speciality_action in _RESOLVED_SPECIALITY_ACTIONS:
                category = placement["target_category_name"]
                speciality = placement["target_speciality_name"]
                if speciality_action == NEW_SPECIALITY:
                    speciality_confidence = _NEW_PROPOSAL_CONFIDENCE
                    reason += f" Speciality '{speciality}' proposed from the source's own grouping."
                else:
                    speciality_confidence = _confidence(
                        placement.get("speciality_score"), placement.get("speciality_semantic_score")
                    )
                    reason += (
                        f" Speciality matches existing '{speciality}' ({placement['speciality_match_type']} match)."
                    )
                confidence = min(c for c in (category_confidence, speciality_confidence) if c is not None)
            else:
                # Category is placed but no speciality could be resolved
                # under it (e.g. still under review) - the contract requires
                # all three or none, so this record stays fully unresolved
                # even though the category itself is known; `reason` still
                # says so for a human reading the record.
                reason += " No speciality could be resolved for this placement."

        records.append(
            ClassificationOutput(
                source_dataset=source_dataset,
                tag_id=str(placement["tag_id"]),
                original_tag=placement["tag_name"],
                original_classification="classified",
                category=category,
                speciality=speciality,
                confidence=confidence,
                reason=reason,
            )
        )
    return records
