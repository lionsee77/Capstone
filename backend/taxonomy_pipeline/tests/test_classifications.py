import pytest
import yaml
from fastapi.testclient import TestClient

from taxonomy_pipeline.api.app import app
from taxonomy_pipeline.config import DatasetConfig, load_config
from taxonomy_pipeline.pipeline import run_pipeline
from taxonomy_pipeline.taxonomy import load_dataset
from tests.conftest import FIXTURES


def config_payload(classification_paths):
    return {
        "datasets": [
            {"name": "original", "paths": [str(FIXTURES / "mini_original.sql")]},
            *[{"name": kind, "type": kind, "paths": [str(path)]}
              for kind, path in classification_paths.items()],
        ],
        "metrics": [{"id": "breadth", "params": {"target": "original", "matching_method": "lexical"}}],
    }


def test_relative_yaml_paths_and_counts(classification_paths, tmp_path):
    payload = config_payload(classification_paths)
    for dataset in payload["datasets"][1:]:
        dataset["paths"] = [f"{dataset['name']}.xlsx"]
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(payload))
    report = run_pipeline(load_config(path))
    assert report.status == "done"
    assert report.results[0].status == "ok"
    summaries = {d.name: d.counts for d in report.datasets}
    assert summaries["ssic"]["division"] == 1
    assert summaries["ssoc"]["minor_group"] == 1


@pytest.mark.parametrize("kind", ["ssic", "ssoc"])
@pytest.mark.parametrize("paths", [[], ["a.xlsx", "b.xlsx"]])
def test_requires_exactly_one_path(kind, paths):
    with pytest.raises(ValueError, match="exactly one"):
        load_dataset(DatasetConfig(name=kind, type=kind, paths=paths))


@pytest.mark.parametrize("profile", ["typography_v1", "legacy"])
def test_api_persists_both_dimensions(classification_paths, isolated_db, profile):
    client = TestClient(app)
    payload = config_payload(classification_paths)
    payload["metrics"][0]["params"]["preprocessing"] = profile
    created = client.post("/runs", json=payload)
    assert created.status_code == 202
    report = client.get(f"/runs/{created.json()['run_id']}").json()
    assert report["status"] == "done"
    result = report["results"][0]
    assert result["status"] == "ok"
    assert result["details"]["industry"]["system"] == "SSIC"
    assert result["details"]["occupational"]["system"] == "SSOC"
    defaults = client.get("/metrics").json()["breadth"]["default_params"]
    assert defaults["industry_level"] == "division"

    assert defaults["preprocessing"] == "typography_v1"
    assert result["details"]["preprocessing"] == profile
    match = result["details"]["industry"]["matched"][0]
    assert match["normalized_reference"] == match["normalized_target"] == "accounting"
