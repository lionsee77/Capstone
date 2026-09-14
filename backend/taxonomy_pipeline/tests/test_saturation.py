from __future__ import annotations

import json

from taxonomy_pipeline.datasets.base import TaxonomyDataset
from taxonomy_pipeline.metrics.saturation import SaturationMetric


def _dataset() -> TaxonomyDataset:
    return TaxonomyDataset(name="original", tags=[{"id": 1, "name": "accounting"}])


def test_skips_cleanly_without_a_corpus():
    result = SaturationMetric().run({"original": _dataset()}, {"dataset": "original"})
    assert result.status == "skipped"


def test_runs_discovery_curve_against_a_provided_corpus(tmp_path):
    corpus_path = tmp_path / "corpus.json"
    corpus_path.write_text(json.dumps(["accounting help needed", "underwater basket weaving"]))

    result = SaturationMetric().run(
        {"original": _dataset()},
        {"dataset": "original", "corpus_path": str(corpus_path), "batch_size": 2},
    )
    assert result.status == "ok"
    assert result.details["total_texts"] == 2
    # "accounting" is covered by the taxonomy; the second text has no match.
    assert "underwater basket weaving" in result.details["sample_uncovered"]
    assert result.details["curve"][-1]["new_in_batch"] == 1
