from unittest.mock import Mock

import numpy as np
import pytest
from fastapi.testclient import TestClient

from taxonomy_pipeline.api.app import app
from taxonomy_pipeline.matching import embeddings
from taxonomy_pipeline.quality.breadth import BreadthMetric
from taxonomy_pipeline.taxonomy.base import TaxonomyDataset
from tests.test_breadth import datasets
from tests.test_classifications import config_payload


@pytest.fixture
def encoder(monkeypatch):
    model = Mock()
    def encode(texts, **kwargs):
        return np.array([[0., 1.] if text in ("Retail", "Nurses") else [1., 0.] for text in texts])
    model.encode.side_effect = encode
    monkeypatch.setattr(embeddings, "_get_model", lambda: model)
    return model


def run(data=None, **params):
    return BreadthMetric().run(datasets() if data is None else data, {"target": "original", **params})


def test_semantic_default_metadata_candidates_and_no_mutation(encoder):
    data = datasets()
    before = {name: ds.model_dump() for name, ds in data.items()}
    result = run(data)
    assert result.status == "ok"
    assert "semantic coverage (uncalibrated)" in result.summary
    assert result.details["matching_method"] == "semantic"
    assert result.details["preprocessing"] == "semantic_labels_v1"
    assert result.details["model_revision"] == embeddings.MODEL_REVISION
    assert result.details["threshold_calibrated"] is False
    for dimension in ("industry", "occupational"):
        detail = result.details[dimension]
        assert detail["coverage"] == 0.5
        assert detail["threshold"] == 0.6
        assert detail["score_type"] == "cosine_similarity"
        assert detail["target_node_count"] == 1
        assert detail["matched"][0]["score"] == 1.0
        assert detail["matched"][0]["normalized_reference"]
        assert detail["unmatched"][0]["candidates"][0]["score"] == 0.0
    assert {name: ds.model_dump() for name, ds in data.items()} == before
    encoder.encode.assert_called_once()
    texts = encoder.encode.call_args.args[0]
    assert len(texts) == len(set(texts)) == 4
    assert encoder.encode.call_args.kwargs["batch_size"] == 32
    assert encoder.encode.call_args.kwargs["normalize_embeddings"] is True


def test_top_k_stable_ties_and_duplicate_target_rows(encoder):
    data = datasets()
    data["original"].categories = [
        {"id": i, "name": "Accounting", "service_id": 1} for i in range(7)
    ]
    detail = run(data).details["industry"]
    assert detail["target_node_count"] == 7
    match = detail["matched"][0]
    assert match["target_id"] == 0
    assert [c["target_id"] for c in match["candidates"]] == list(range(5))
    assert len(run(data, top_k=2).details["industry"]["matched"][0]["candidates"]) == 2


def test_unrounded_threshold_and_negative_similarity(monkeypatch):
    def encode(texts):
        return {text: np.array({
            "Accounting": [1., 0.], "Retail": [0.59999, (1 - 0.59999**2)**0.5],
            "Accountants": [1., 0.], "Nurses": [-1., 0.],
        }[text]) for text in texts}
    monkeypatch.setattr(embeddings, "encode_labels", encode)
    result = run()
    miss = result.details["industry"]["unmatched"][0]
    assert miss["candidates"][0]["score"] == 0.6  # rounded display must not accept it
    assert result.details["occupational"]["unmatched"][0]["candidates"][0]["score"] == -1
    assert run(semantic_threshold=-1).details["occupational"]["coverage"] == 1
    assert run(semantic_threshold=1).details["industry"]["matched_count"] == 1


def test_ranks_best_target_first(encoder):
    result = run(industry_service_id=2)
    assert result.details["industry"]["matched"][0]["title"] == "Retail"
    data = datasets()
    data["original"].categories[1]["service_id"] = 1
    detail = run(data).details["industry"]
    assert [m["target_id"] for m in detail["matched"]] == [1, 2]


def test_empty_targets_and_reference_levels_do_not_load_model(monkeypatch):
    def fail():
        pytest.fail("No model should be needed")
    monkeypatch.setattr(embeddings, "_get_model", fail)
    data = datasets()
    data["original"] = TaxonomyDataset(name="original", categories=[
        {"id": 1, "name": " ", "service_id": 1}])
    result = run(data, semantic_threshold=-1)
    assert result.details["industry"]["coverage"] == 0
    assert result.details["industry"]["unmatched"][0]["candidates"] == []
    result = run(industry_level="group", occupational_level="occupation")
    assert result.details["industry"]["coverage"] is None
    assert result.details["occupational"]["coverage"] is None


def test_lexical_does_not_load_model_or_validate_semantic_options(monkeypatch):
    monkeypatch.setattr(embeddings, "_get_model", lambda: pytest.fail("Model loaded"))
    assert run(matching_method="lexical", semantic_threshold="bad", top_k=0).status == "ok"


def test_semantic_ignores_lexical_options(encoder):
    assert run(threshold=75, preprocessing="not-used").status == "ok"


@pytest.mark.parametrize("params", [
    {"matching_method": "unknown"}, {"matching_method": None},
    {"semantic_threshold": -1.01}, {"semantic_threshold": 1.01},
    {"semantic_threshold": float("nan")}, {"semantic_threshold": float("inf")},
    {"semantic_threshold": None}, {"semantic_threshold": "bad"}, {"semantic_threshold": True},
    {"top_k": 0}, {"top_k": -1}, {"top_k": 1.5}, {"top_k": "5"}, {"top_k": True},
    {"industry_level": "bad"}, {"occupational_level": "bad"},
])
def test_invalid_options_fail_before_encoding(params, encoder):
    assert run(**params).status == "error"
    encoder.encode.assert_not_called()


@pytest.mark.parametrize("error", [ImportError("dependency missing"),
                                   OSError("download failed"),
                                   RuntimeError("inference failed")])
def test_model_failures_are_metric_errors(monkeypatch, error):
    def fail():
        raise error
    monkeypatch.setattr(embeddings, "_get_model", fail)
    result = run()
    assert result.status == "error"
    assert "Semantic embedding failed" in result.summary
    assert str(error) in result.summary


@pytest.mark.parametrize("vectors", [np.zeros((4, 2)), np.full((4, 2), np.nan), np.zeros((3, 2))])
def test_invalid_embeddings_are_errors(encoder, vectors):
    encoder.encode.side_effect = None
    encoder.encode.return_value = vectors
    assert run().status == "error"


def test_semantic_text_preserves_readable_label():
    assert embeddings.semantic_text("  ＣＯＯ  /  Ships’\u00a0Deck ") == "COO / Ships’ Deck"


def test_api_persists_semantic_defaults_and_candidates(encoder, classification_paths, isolated_db):
    client = TestClient(app)
    payload = config_payload(classification_paths)
    payload["metrics"][0]["params"].pop("matching_method")
    created = client.post("/runs", json=payload)
    assert created.status_code == 202
    report = client.get(f"/runs/{created.json()['run_id']}").json()
    result = report["results"][0]
    assert result["status"] == "ok"
    assert result["details"]["matching_method"] == "semantic"
    assert result["details"]["model_revision"] == embeddings.MODEL_REVISION
    match = result["details"]["industry"]["matched"][0]
    assert match["candidates"][0]["normalized_target"] == "Accounting"
    defaults = client.get("/metrics").json()["breadth"]["default_params"]
    assert defaults["matching_method"] == "semantic"
    assert defaults["semantic_threshold"] == 0.6
    assert defaults["top_k"] == 5
