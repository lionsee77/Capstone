"""The browse use cases behind `/taxonomy`: resolve a past run's dataset, then search or describe it."""
from __future__ import annotations

from pathlib import Path

import pytest

from taxonomy_pipeline import db
from taxonomy_pipeline.app import browse
from taxonomy_pipeline.config import DatasetConfig, PipelineConfig
from taxonomy_pipeline.models import RunReport
from tests.conftest import FIXTURES


@pytest.fixture
def run_id(isolated_db: Path, classification_paths: dict[str, Path]) -> str:
    config = PipelineConfig(datasets=[
        DatasetConfig(name="original", paths=[str(FIXTURES / "mini_original.sql")]),
        DatasetConfig(name="ssic", type="ssic", paths=[str(classification_paths["ssic"])]),
    ], metrics=[])
    report = RunReport(status="done")
    db.save_run(report, config_json=config.model_dump_json())
    return report.run_id


def test_search_clamps_the_limit_to_between_one_and_the_maximum(run_id: str) -> None:
    assert len(browse.search_run_dataset(run_id, "original", limit=0)) == 1
    assert len(browse.search_run_dataset(run_id, "original", limit=10_000)) <= browse.MAX_SEARCH_LIMIT


def test_search_finds_concepts_by_normalized_name(run_id: str) -> None:
    results = browse.search_run_dataset(run_id, "original", q="TAX")
    assert {(r.level, r.name) for r in results} == {("specialities", "Tax Advisory"), ("tags", "tax")}


def test_concept_detail_reads_the_runs_dataset(run_id: str) -> None:
    speciality = browse.search_run_dataset(run_id, "original", q="Tax Advisory", level="specialities")[0]
    detail = browse.concept_detail(run_id, "original", "specialities", str(speciality.id))
    assert detail.name == "Tax Advisory" and detail.paths[0].speciality.name == "Tax Advisory"


@pytest.mark.parametrize(("run", "dataset", "message"), [
    ("missing-run", "original", "run not found"),
    (None, "nope", "not loaded by run"),
    (None, "ssic", "is not a taxonomy dataset"),
])
def test_an_unknown_run_or_dataset_is_not_found(run_id: str, run: str | None, dataset: str, message: str) -> None:
    with pytest.raises(browse.NotFound, match=message):
        browse.search_run_dataset(run or run_id, dataset)
