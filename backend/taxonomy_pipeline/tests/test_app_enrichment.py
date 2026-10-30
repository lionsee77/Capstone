"""The enrichment use cases hold each command's checks, writes and warnings, so the CLI only formats them."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from taxonomy_pipeline.app import enrichment as use_cases
from taxonomy_pipeline.enrichment.integrate import Integration, IntegrationError
from taxonomy_pipeline.enrichment.runner import run_merge_preview
from taxonomy_pipeline.taxonomy.base import TaxonomyDataset

FIXTURES = Path(__file__).parent / "fixtures"
CLASSIFICATION = FIXTURES / "classification"


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({
        "datasets": [{"name": "original", "paths": [str(FIXTURES / "mini_original.sql")]}],
        "metrics": [{"id": "integrity", "params": {"dataset": "original"}}],
    }), encoding="utf-8")
    return path


@pytest.fixture
def merge_run(tmp_path: Path) -> Path:
    """A real merge run over unstamped producer files, so its run.json records no saturation run."""
    baseline = TaxonomyDataset.model_validate_json((CLASSIFICATION / "taxonomy.json").read_text(encoding="utf-8"))
    out_dir = tmp_path / "merge"
    run_merge_preview([CLASSIFICATION / "case_a.json"], baseline, out_dir=out_dir)
    return out_dir


def test_merge_preview_needs_at_least_one_input(config_path: Path) -> None:
    with pytest.raises(ValueError, match="at least one --input"):
        use_cases.merge_preview(config_path, [], baseline="original")


def test_merge_preview_writes_to_a_timestamped_folder_by_default(
    config_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    run = use_cases.merge_preview(config_path, [CLASSIFICATION / "case_a.json"], baseline="original")
    assert run.out_dir.parent == Path("merge_runs")
    assert (tmp_path / run.out_dir / "run.json").is_file()


def test_bulk_decide_refuses_to_overwrite_an_output_it_was_not_asked_to_extend(
    config_path: Path, tmp_path: Path,
) -> None:
    output = tmp_path / "decisions.json"
    output.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="pass it as --decisions"):
        use_cases.bulk_decide(config_path, tmp_path, "report.json", baseline="original", incoming="skillsfuture",
                              output=output)


def test_bulk_decide_refuses_a_merge_run_with_no_saturation_run(
    config_path: Path, merge_run: Path, tmp_path: Path, saturation_report,
) -> None:
    report = saturation_report([{"recommended_action": "ADD_CANDIDATE", "parent_status": "NO_PARENT",
                                 "incoming_ids": [1]}])
    with pytest.raises(ValueError, match="records no saturation run"):
        use_cases.bulk_decide(config_path, merge_run, report, baseline="original", incoming="skillsfuture",
                              output=tmp_path / "decisions.json")
    assert not (tmp_path / "decisions.json").exists()


def test_integrate_returns_its_warnings_and_records_the_merge_run_as_provenance(
    config_path: Path, merge_run: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = {}

    def fake_integrate(resolution, dataset, *, out_dir, scoring_config, provenance, actor):
        seen.update(dataset=dataset.name, provenance=provenance, scoring=scoring_config.metrics[0].id)
        return Integration(out_dir=Path(out_dir), manifest={
            "counts": {}, "omitted_records": {"invalid": 2},
            "exact_duplicate_rows": {"tags": {"baseline": 1, "enriched": 3}, "categories": {"baseline": 2,
                                                                                            "enriched": 2}},
        })

    monkeypatch.setattr(use_cases, "integrate_snapshot", fake_integrate)
    outcome = use_cases.integrate(config_path, merge_run, baseline="original", out_dir=tmp_path / "snapshot")

    run_info = json.loads((merge_run / "run.json").read_text(encoding="utf-8"))
    assert seen["provenance"] == {"merge_run": str(merge_run), "scoring_config": str(config_path), **run_info}
    assert seen["dataset"] == "original" and seen["scoring"] == "integrity"
    assert len(outcome.warnings) == 2
    assert outcome.warnings[0].startswith("2 input record(s) were omitted before review")
    assert outcome.warnings[1].startswith("tags: rows in exact-duplicate name clusters rose from 1 to 3")


def test_integrate_names_a_missing_baseline_dataset(config_path: Path, merge_run: Path) -> None:
    with pytest.raises(IntegrationError, match="declares no dataset named 'nope'"):
        use_cases.integrate(config_path, merge_run, baseline="nope")
