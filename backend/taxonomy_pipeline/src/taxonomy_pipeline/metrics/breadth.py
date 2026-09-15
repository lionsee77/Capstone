"""Estimated lexical industry (SSIC) and occupational (SSOC) coverage."""
from __future__ import annotations

import math
from typing import Any

from rapidfuzz import fuzz, process

from ..datasets.base import LoadedDataset, TaxonomyDataset
from ..datasets.ssic import SsicReference
from ..datasets.ssoc import SsocReference
from ..models import MetricResult
from .base import Metric
from .integrity import normalize


def _coverage(
    reference: SsicReference | SsocReference, reference_name: str,
    level: str, target_rows: list[dict], threshold: float,
) -> dict[str, Any]:
    # Count reference identities, not repeated titles or target names.
    records = list({record.code: record for record in reference.at_level(level)}.values())
    targets = [row for row in target_rows if normalize(row.get("name"))]
    names = [normalize(row["name"]) for row in targets]
    matched, unmatched = [], []
    for record in records:
        best = process.extractOne(normalize(record.title), names, scorer=fuzz.ratio) if names else None
        entry = {"code": record.code, "title": record.title}
        if best and best[1] >= threshold:
            target = targets[best[2]]
            matched.append({
                **entry, "target_id": target.get("id"),
                "matched_to": target["name"], "score": round(best[1], 1),
            })
        else:
            unmatched.append(entry)
    return {
        "reference": reference_name, "system": reference.system, "version": reference.version,
        "level": level, "threshold": threshold, "target_node_count": len(targets),
        "matched_count": len(matched), "total_count": len(records),
        "coverage": round(len(matched) / len(records), 3) if records else None,
        "matched": matched, "unmatched": unmatched,
    }


class BreadthMetric(Metric):
    id = "breadth"
    description = "Estimated lexical industry coverage against SSIC and occupational coverage against SSOC."
    default_params = {
        "target": None, "ssic_reference": "ssic", "ssoc_reference": "ssoc",
        "industry_level": "division", "occupational_level": "minor_group",
        "industry_service_id": 1, "threshold": 75.0,
    }

    def run(self, datasets: dict[str, LoadedDataset], params: dict[str, Any]) -> MetricResult:
        p = self.resolve_params(params)
        if "reference" in p:
            return MetricResult(metric_id=self.id, status="error", summary=(
                "The breadth 'reference' parameter was replaced: use ssic_reference and "
                "ssoc_reference naming datasets of type ssic and ssoc."
            ))
        resolved = self.require_datasets(
            datasets, {"target": p["target"], "ssic": p["ssic_reference"], "ssoc": p["ssoc_reference"]},
            {"target": TaxonomyDataset, "ssic": SsicReference, "ssoc": SsocReference},
        )
        if isinstance(resolved, MetricResult):
            return resolved
        try:
            threshold = float(p["threshold"])
            if not math.isfinite(threshold) or not 0 <= threshold <= 100:
                raise ValueError("threshold must be finite and between 0 and 100")
            target = resolved["target"]
            industry = _coverage(
                resolved["ssic"], p["ssic_reference"], p["industry_level"],
                [row for row in target.categories if row.get("service_id") == p["industry_service_id"]],
                threshold,
            )
            occupational = _coverage(
                resolved["ssoc"], p["ssoc_reference"], p["occupational_level"],
                target.specialities, threshold,
            )
        except (TypeError, ValueError) as exc:
            return MetricResult(metric_id=self.id, status="error", summary=str(exc))

        summaries = []
        for label, result in (("industry", industry), ("occupational", occupational)):
            if result["coverage"] is None:
                summaries.append(f"{label}: no reference records at {result['level']} level")
            else:
                summaries.append(f"{label}: {result['matched_count']}/{result['total_count']} "
                                 f"({result['coverage']:.0%})")
        return MetricResult(
            metric_id=self.id,
            summary=f"{p['target']}: estimated lexical coverage — " + "; ".join(summaries),
            details={"target": p["target"], "industry_service_id": p["industry_service_id"],
                     "industry": industry, "occupational": occupational},
        )
