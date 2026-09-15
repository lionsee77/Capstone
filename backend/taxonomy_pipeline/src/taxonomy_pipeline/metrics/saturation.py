"""Saturation: discovery-curve completeness against real usage - process
gig/showcase text in batches and track how often the text has no matching
taxonomy term, expecting that rate to flatten as the taxonomy matures.

This project doesn't have real (or synthetic) gig/showcase text yet (see the
`senseigigs-taxonomy-validation-pipeline` project memory) - without
`params.corpus_path` this metric reports itself as "skipped" rather than
failing the whole run, so it's safe to leave in every config.

The matching here is a naive word-level fuzzy match against category/
speciality/tag names - a deliberate baseline, not the final method. Swap it
for the shared sentence-transformers embedding layer (see the pipeline's
README) once one exists, since pure lexical matching can't see e.g. 'M&A'
covering 'Mergers and Acquisitions'.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz, process

from ..datasets.base import LoadedDataset
from ..models import MetricResult
from .base import Metric
from .integrity import normalize


def _load_corpus(path: str) -> list[str]:
    p = Path(path)
    if p.suffix == ".json":
        return [str(x) for x in json.loads(p.read_text())]
    return [line.strip() for line in p.read_text().splitlines() if line.strip()]


class SaturationMetric(Metric):
    id = "saturation"
    description = (
        "Discovery-curve completeness: cumulative rate of corpus text with no "
        "matching taxonomy term, in batches. Requires params.corpus_path; "
        "reports 'skipped' without it."
    )
    default_params = {"dataset": None, "corpus_path": None, "batch_size": 25, "threshold": 80.0}

    def run(self, datasets: dict[str, LoadedDataset], params: dict[str, Any]) -> MetricResult:
        p = self.resolve_params(params)
        dataset_name = p.get("dataset")
        corpus_path = p.get("corpus_path")

        resolved = self.require_datasets(datasets, {"dataset": dataset_name})
        if isinstance(resolved, MetricResult):
            return resolved
        if not corpus_path or not Path(corpus_path).exists():
            return MetricResult(
                metric_id=self.id,
                status="skipped",
                summary=(
                    "no gig/showcase text corpus available yet - pass params.corpus_path "
                    "once real or synthetic text exists"
                ),
            )

        ds = resolved["dataset"]
        vocabulary = [
            normalize(row.get("name"))
            for row in (*ds.categories, *ds.specialities, *ds.tags)
            if row.get("name")
        ]
        if not vocabulary:
            return MetricResult(
                metric_id=self.id,
                status="error",
                summary=f"dataset {dataset_name!r} has no named terms to match against",
            )

        texts = _load_corpus(corpus_path)
        threshold = float(p["threshold"])
        batch_size = int(p["batch_size"])

        curve = []
        uncovered_samples: set[str] = set()
        total_new = 0
        for batch_start in range(0, len(texts), batch_size):
            batch = texts[batch_start : batch_start + batch_size]

            # One candidate list per text (its words, plus the whole
            # normalized text), flattened with an owner index so the whole
            # batch's coverage can be scored in a single vectorized cdist
            # call instead of one process.extractOne per word.
            owners: list[int] = []
            candidates: list[str] = []
            for text_index, text in enumerate(batch):
                for candidate in normalize(text).split() + [normalize(text)]:
                    owners.append(text_index)
                    candidates.append(candidate)

            best_score_per_candidate = process.cdist(candidates, vocabulary, scorer=fuzz.ratio).max(
                axis=1
            )
            best_score_per_text = [0.0] * len(batch)
            for owner, score in zip(owners, best_score_per_candidate, strict=True):
                best_score_per_text[owner] = max(best_score_per_text[owner], score)

            batch_new = 0
            for text, score in zip(batch, best_score_per_text, strict=True):
                if score < threshold:
                    batch_new += 1
                    uncovered_samples.add(normalize(text)[:80])
            total_new += batch_new
            processed = batch_start + len(batch)
            curve.append(
                {
                    "batch_end": processed,
                    "new_in_batch": batch_new,
                    "cumulative_new_rate": round(total_new / processed, 3),
                }
            )

        details = {
            "dataset": dataset_name,
            "corpus_path": corpus_path,
            "total_texts": len(texts),
            "curve": curve,
            "sample_uncovered": sorted(uncovered_samples)[:20],
        }
        final_rate = curve[-1]["cumulative_new_rate"] if curve else None
        summary = (
            f"{dataset_name}: {total_new}/{len(texts)} corpus texts had no matching taxonomy "
            f"term (final cumulative rate {final_rate})."
        )
        return MetricResult(metric_id=self.id, status="ok", summary=summary, details=details)
