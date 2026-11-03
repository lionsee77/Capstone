"""Estimated semantic or lexical industry (SSIC) and occupational (SSOC) coverage."""
from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import numpy as np
from rapidfuzz import fuzz, process

from ..matching import embeddings
from ..matching.preprocessing import get_normalizer
from ..models import Measure, MetricResult
from ..taxonomy.base import LoadedDataset, TaxonomyDataset
from ..taxonomy.references.ssic import SsicReference
from ..taxonomy.references.ssoc import SsocReference
from .base import Metric


def _coverage(
    reference: SsicReference | SsocReference, reference_name: str,
    level: str, target_rows: list[dict], threshold: float,
    normalize: Callable[[str | None], str],
) -> dict[str, Any]:
    # Count reference identities, not repeated titles or target names.
    records = list({record.code: record for record in reference.at_level(level)}.values())
    named_targets = [(row, normalize(row.get("name"))) for row in target_rows]
    targets = [row for row, name in named_targets if name]
    names = [name for _, name in named_targets if name]
    matched, unmatched = [], []
    for record in records:
        normalized_reference = normalize(record.title)
        best = process.extractOne(normalized_reference, names, scorer=fuzz.ratio) if names else None
        entry = {"code": record.code, "title": record.title}
        if best and best[1] >= threshold:
            target = targets[best[2]]
            matched.append({
                **entry, "target_id": target.get("id"),
                "matched_to": target["name"], "score": round(best[1], 1),
                "normalized_reference": normalized_reference,
                "normalized_target": names[best[2]],
            })
        else:
            unmatched.append(entry)
    return {
        "reference": reference_name, "system": reference.system, "version": reference.version,
        "level": level, "threshold": threshold, "target_node_count": len(targets),
        "matched_count": len(matched), "total_count": len(records),
        "coverage": round(len(matched) / len(records), 3) if records else None,
        "score_type": "fuzz_ratio",
        "matched": matched, "unmatched": unmatched,
    }


def _semantic_coverage(
    reference: SsicReference | SsocReference, reference_name: str,
    level: str, target_rows: list[dict], threshold: float, top_k: int,
    vectors: dict[str, np.ndarray],
) -> dict[str, Any]:
    """Score reference identities, retaining candidate evidence below the cutoff."""
    records = list({record.code: record for record in reference.at_level(level)}.values())
    targets = [(row, embeddings.semantic_text(row.get("name"))) for row in target_rows]
    targets = [(row, text) for row, text in targets if text]
    scores = None
    if records and targets:
        reference_vectors = np.stack([vectors[embeddings.semantic_text(r.title)] for r in records])
        target_vectors = np.stack([vectors[text] for _, text in targets])
        scores = np.clip(reference_vectors @ target_vectors.T, -1.0, 1.0)
    matched, unmatched = [], []
    for index, record in enumerate(records):
        candidates = []
        order = np.argsort(-scores[index], kind="stable")[:top_k] if scores is not None else []
        for target_index in order:
            row, text = targets[target_index]
            candidates.append({
                "target_id": row.get("id"), "matched_to": row["name"],
                "score": round(float(scores[index, target_index]), 4),
                "normalized_target": text,
            })
        entry = {
            "code": record.code, "title": record.title,
            "normalized_reference": embeddings.semantic_text(record.title),
            "candidates": candidates,
        }
        if candidates and float(scores[index, order[0]]) >= threshold:
            matched.append({**entry, **candidates[0]})
        else:
            unmatched.append(entry)
    return {
        "reference": reference_name, "system": reference.system, "version": reference.version,
        "level": level, "threshold": threshold, "target_node_count": len(targets),
        "matched_count": len(matched), "total_count": len(records),
        "coverage": round(len(matched) / len(records), 3) if records else None,
        "score_type": "cosine_similarity", "threshold_calibrated": False,
        "matched": matched, "unmatched": unmatched,
    }


class BreadthMetric(Metric):
    id = "breadth"
    subject_param = "target"
    description = "Estimated semantic or lexical industry coverage against SSIC and occupational coverage against SSOC."
    default_params = {
        "target": None, "ssic_reference": "ssic", "ssoc_reference": "ssoc",
        "industry_level": "division", "occupational_level": "minor_group",
        "industry_service_id": 1, "threshold": 75.0,
        "preprocessing": "typography_v1",
        "matching_method": "semantic", "semantic_threshold": 0.60, "top_k": 5,
    }

    def definition(self) -> dict[str, Any]:
        # The sentence model and its text profile are fixed in code, not params.
        return {
            **super().definition(),
            "embedding": {
                "model": embeddings.MODEL_ID, "revision": embeddings.MODEL_REVISION,
                "text_profile": embeddings.TEXT_PROFILE,
            },
        }

    def measures(self, result: MetricResult) -> list[Measure]:
        """Reference nodes covered, higher is better: as a count, so a gain of one node shows, and as the
        coverage rate."""
        measures = []
        for dimension, reference in (("industry", "SSIC"), ("occupational", "SSOC")):
            section = result.details.get(dimension, {})
            measures += [
                Measure(key=f"{dimension}.matched_count", label=f"{reference} {dimension} nodes covered",
                        value=section.get("matched_count"), better="higher"),
                Measure(key=f"{dimension}.coverage", label=f"{dimension.capitalize()} coverage",
                        value=section.get("coverage"), better="higher", unit="rate"),
            ]
        return measures

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
            method = p["matching_method"]
            if method not in ("semantic", "lexical"):
                raise ValueError("matching_method must be 'semantic' or 'lexical'")
            target = resolved["target"]
            selections = [
                (resolved["ssic"], p["ssic_reference"], p["industry_level"],
                 [row for row in target.categories if row.get("service_id") == p["industry_service_id"]]),
                (resolved["ssoc"], p["ssoc_reference"], p["occupational_level"], target.specialities),
            ]
            metadata = {"matching_method": method}
            if method == "lexical":
                normalize = get_normalizer(p["preprocessing"])
                threshold = float(p["threshold"])
                if not math.isfinite(threshold) or not 0 <= threshold <= 100:
                    raise ValueError("threshold must be finite and between 0 and 100")
                metadata["preprocessing"] = p["preprocessing"]
                industry, occupational = [_coverage(*selection, threshold, normalize) for selection in selections]
            else:
                threshold = float(p["semantic_threshold"])
                if (isinstance(p["semantic_threshold"], bool)
                        or not math.isfinite(threshold) or not -1 <= threshold <= 1):
                    raise ValueError("semantic_threshold must be finite and between -1 and 1")
                top_k = p["top_k"]
                if type(top_k) is not int or top_k < 1:
                    raise ValueError("top_k must be a positive integer")
                texts = []
                for reference, _, level, rows in selections:
                    records = reference.at_level(level)
                    names = [embeddings.semantic_text(row.get("name")) for row in rows]
                    if records and any(names):
                        texts.extend(embeddings.semantic_text(record.title) for record in records)
                        texts.extend(names)
                vectors = embeddings.encode_labels(texts)
                industry, occupational = [
                    _semantic_coverage(*selection, threshold, top_k, vectors) for selection in selections
                ]
                metadata.update({
                    "preprocessing": embeddings.TEXT_PROFILE,
                    "model": embeddings.MODEL_ID, "model_revision": embeddings.MODEL_REVISION,
                    "threshold_calibrated": False, "top_k": top_k,
                })
        except (TypeError, ValueError, RuntimeError) as exc:
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
            summary=f"{p['target']}: estimated {method} coverage"
                    + (" (uncalibrated)" if method == "semantic" else "") + " — " + "; ".join(summaries),
            details={"target": p["target"], "industry_service_id": p["industry_service_id"],
                     **metadata,
                     "industry": industry, "occupational": occupational},
        )
