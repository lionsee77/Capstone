"""Case A: place new tags that bring their own source category and speciality.

Which tags are new, or possibly new, comes from a saved saturation report (see
`routing.py`), the same file Case B reads, so both producers split one result.
Possibly new (`REVIEW_AMBIGUOUS`) tags are placed the same way and flagged for review. Placement itself is
`category_adaptation`; this module only routes tags into it and serializes the
placements into the shared classification contract.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..taxonomy.base import LoadedDataset
from .category_adaptation import CategoryAdaptationMetric
from .from_category_adaptation import build_classification_outputs
from .models import CLASSIFICATION_OUTPUTS
from .routing import RUN_ID_FIELD, case_a_tag_ids, load_saturation, mark_ambiguous


class CaseAError(RuntimeError):
    """category_adaptation could not run; the message is its summary."""


def run_case_a(
    datasets: dict[str, LoadedDataset],
    report_path: str | Path,
    *,
    baseline: str,
    incoming: str,
    semantic_model: str | None = None,
    category_scoped_semantic_threshold: float | None = None,
    one_category_per_tag: bool = False,
) -> list[dict[str, Any]]:
    """Return contract-valid records, each stamped with the saturation run they came from.

    The two placement options (#132) are off by default; see `category_adaptation`.
    """
    saturation = load_saturation(report_path, incoming)
    params: dict[str, Any] = {
        "baseline": baseline, "incoming": incoming, "new_tag_ids": case_a_tag_ids(saturation),
        "category_scoped_semantic_threshold": category_scoped_semantic_threshold,
        "one_category_per_tag": one_category_per_tag,
    }
    if semantic_model:
        params["semantic_model"] = semantic_model
    result = CategoryAdaptationMetric().run(datasets, params)
    if result.status != "ok":
        raise CaseAError(f"category_adaptation failed: {result.summary}")

    outputs = build_classification_outputs(result.details["placements"], source_dataset=incoming)
    records = mark_ambiguous(CLASSIFICATION_OUTPUTS.dump_python(outputs, mode="json"), saturation)
    CLASSIFICATION_OUTPUTS.validate_python(records)  # round-trip check before anything is written
    return [{**record, RUN_ID_FIELD: saturation.run_id} for record in records]
