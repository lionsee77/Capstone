"""The synonym use cases check the concept against a real taxonomy and are what the CLI calls."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from taxonomy_pipeline.app import synonyms as use_cases
from taxonomy_pipeline.audit import ChangeQuery, read_changes
from taxonomy_pipeline.cli import main
from taxonomy_pipeline.synonyms import SynonymCreate, SynonymQuery, UnknownConcept
from tests.conftest import FIXTURES


@pytest.fixture
def config(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({
        "datasets": [{"name": "original", "type": "sql_dump", "paths": [str(FIXTURES / "mini_original.sql")]}],
        "metrics": [{"id": "integrity", "params": {"dataset": "original"}}],
    }), encoding="utf-8")
    return path


def _request(**changes: str) -> SynonymCreate:
    fields = {"dataset": "original", "concept_level": "tags", "concept_id": "101", "term": "Goods and Services Tax",
              "reason": "Expansion of the acronym", "actor": "reviewer", **changes}
    return SynonymCreate(**fields)


def test_adds_a_synonym_for_a_concept_found_through_a_config(isolated_db: Path, config: Path) -> None:
    synonym = use_cases.add_synonym(_request(), config_path=config)
    assert (synonym.concept_name, synonym.dataset) == ("gst", "original")
    assert synonym.snapshot_id.startswith("sha256:")
    assert use_cases.list_synonyms(SynonymQuery(term="goods and services tax")).synonyms == [synonym]


@pytest.mark.parametrize(("changes", "message"), [
    ({"concept_id": "999"}, "no tag with ID 999"),
    ({"dataset": "missing"}, "declares no dataset named 'missing'"),
])
def test_an_unknown_dataset_or_concept_is_reported_and_nothing_is_stored(
    isolated_db: Path, config: Path, changes: dict, message: str,
) -> None:
    with pytest.raises(UnknownConcept, match=message):
        use_cases.add_synonym(_request(**changes), config_path=config)
    assert not isolated_db.exists()


def test_needs_exactly_one_way_to_find_the_taxonomy(isolated_db: Path, config: Path) -> None:
    for kwargs in ({}, {"config_path": config, "run_id": "run"}):
        with pytest.raises(ValueError, match="exactly one"):
            use_cases.add_synonym(_request(), **kwargs)


@pytest.mark.parametrize("field", ["reason", "actor"])
def test_removal_refuses_a_blank_reason_or_actor(isolated_db: Path, config: Path, field: str) -> None:
    synonym = use_cases.add_synonym(_request(), config_path=config)
    kwargs = {"reason": "Wrong", "actor": "lead", field: "   "}
    with pytest.raises(ValueError, match="non-blank reason and actor"):
        use_cases.remove_synonym(synonym.synonym_id, **kwargs)
    assert use_cases.list_synonyms(SynonymQuery()).total == 1


def test_cli_adds_lists_and_removes_through_the_same_use_cases(
    isolated_db: Path, config: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["synonym-add", "--config", str(config), "--level", "tags", "--concept-id", "101",
                 "--term", "GST tax", "--reason", "Seen in gig posts", "--actor", "reviewer"]) == 0
    assert "'GST tax' -> 'gst'" in capsys.readouterr().out
    synonym_id = use_cases.list_synonyms(SynonymQuery()).synonyms[0].synonym_id

    assert main(["synonyms", "--level", "tags"]) == 0
    assert '"term": "GST tax"' in capsys.readouterr().out

    assert main(["synonym-remove", synonym_id, "--reason", "Not used", "--actor", "lead"]) == 0
    assert use_cases.list_synonyms(SynonymQuery()).total == 0
    assert [e.action for e in read_changes(ChangeQuery()).changes] == ["synonym_add", "synonym_remove"]


def test_cli_reports_a_refused_synonym_and_exits_non_zero(
    isolated_db: Path, config: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["synonym-add", "--config", str(config), "--level", "tags", "--concept-id", "101",
                 "--term", "GST", "--reason", "Same name"]) == 1
    assert "[FAILED] synonym-add" in capsys.readouterr().err


@pytest.mark.parametrize("text", ["datasets: [unclosed", "datasets: not-a-list\nmetrics: []"])
def test_a_broken_config_is_reported_as_unavailable_not_as_a_missing_concept(
    isolated_db: Path, tmp_path: Path, text: str, capsys: pytest.CaptureFixture[str],
) -> None:
    from taxonomy_pipeline.synonyms import TaxonomyUnavailable

    broken = tmp_path / "broken.yaml"
    broken.write_text(text, encoding="utf-8")
    with pytest.raises(TaxonomyUnavailable, match="couldn't be read"):
        use_cases.add_synonym(_request(), config_path=broken)
    assert main(["synonym-add", "--config", str(broken), "--level", "tags", "--concept-id", "101",
                 "--term", "Goods and Services Tax", "--reason", "x"]) == 1
    assert "[FAILED] synonym-add" in capsys.readouterr().err


def test_a_dataset_whose_file_is_gone_is_unavailable(isolated_db: Path, tmp_path: Path) -> None:
    from taxonomy_pipeline.synonyms import TaxonomyUnavailable

    config = tmp_path / "gone.yaml"
    config.write_text(yaml.safe_dump({"datasets": [{"name": "original", "paths": [str(tmp_path / "gone.sql")]}],
                                      "metrics": [{"id": "integrity", "params": {"dataset": "original"}}]}))
    with pytest.raises(TaxonomyUnavailable, match="couldn't be loaded"):
        use_cases.add_synonym(_request(), config_path=config)
