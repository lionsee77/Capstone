"""The synonym use cases check the concept against a real taxonomy and are what the CLI calls."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from taxonomy_pipeline.app import synonyms as use_cases
from taxonomy_pipeline.audit import ChangeQuery, read_changes
from taxonomy_pipeline.cli import main
from taxonomy_pipeline.synonyms import ConflictingSynonym, InvalidScope, SynonymCreate, SynonymQuery, UnknownConcept
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


def test_a_global_synonym_that_is_another_concepts_name_is_refused_but_a_scoped_one_is_kept(
    isolated_db: Path, config: Path,
) -> None:
    # mini_original: tag 100 "tax" and tag 103 "advisory" both sit in speciality 10 (category 1, Accounting).
    with pytest.raises(ConflictingSynonym, match="already the name of tag 100"):
        use_cases.add_synonym(_request(concept_id="103", term="Tax"), config_path=config)
    assert not use_cases.list_synonyms(SynonymQuery()).synonyms
    scoped = use_cases.add_synonym(
        _request(concept_id="103", term="Tax", scope_level="specialities", scope_id="10"), config_path=config,
    )
    assert scoped.scope == ("specialities", "10")


def test_a_scope_the_concept_is_not_in_is_refused(isolated_db: Path, config: Path) -> None:
    with pytest.raises(InvalidScope, match="doesn't sit inside category 3"):
        use_cases.add_synonym(_request(scope_level="categories", scope_id="3"), config_path=config)


def test_cli_adds_a_scoped_synonym(isolated_db: Path, config: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["synonym-add", "--config", str(config), "--level", "tags", "--concept-id", "103",
                 "--term", "tax", "--reason", "Means advisory in Tax Advisory", "--scope-level", "specialities",
                 "--scope-id", "10"]) == 0
    assert "within specialities 10" in capsys.readouterr().out


def test_resolves_a_term_through_names_and_approved_synonyms(isolated_db: Path, config: Path) -> None:
    use_cases.add_synonym(_request(), config_path=config)  # "Goods and Services Tax" -> tag 101 gst
    use_cases.add_synonym(_request(concept_id="103", term="Tax", scope_level="specialities", scope_id="10"),
                          config_path=config)
    hit = use_cases.resolve_term("goods and services tax", dataset="original", config_path=config)
    assert hit.status == "resolved"
    assert [(m.concept_id, m.via, m.synonym_term) for m in hit.matches] == [
        ("101", "synonym", "Goods and Services Tax"),
    ]
    assert use_cases.resolve_term("tax", dataset="original", config_path=config).matches[0].concept_id == "100"
    inside = use_cases.resolve_term("tax", dataset="original", config_path=config,
                                    within_level="specialities", within_id="10")
    assert [(m.concept_id, m.via) for m in inside.matches] == [("103", "synonym")]
    assert use_cases.resolve_term("payroll", dataset="original", config_path=config).status == "unknown"


def test_resolving_inside_an_unknown_branch_is_refused(isolated_db: Path, config: Path) -> None:
    with pytest.raises(UnknownConcept, match="no speciality with ID 99"):
        use_cases.resolve_term("tax", dataset="original", config_path=config,
                               within_level="specialities", within_id="99")
    with pytest.raises(ValueError, match="both within_level and within_id"):
        use_cases.resolve_term("tax", dataset="original", config_path=config, within_level="categories")
    with pytest.raises(UnknownConcept):  # an empty ID is a branch that doesn't exist, not "no branch"
        use_cases.resolve_term("tax", dataset="original", config_path=config, within_level="categories",
                               within_id="")


def test_removed_synonyms_do_not_resolve(isolated_db: Path, config: Path) -> None:
    synonym = use_cases.add_synonym(_request(), config_path=config)
    use_cases.remove_synonym(synonym.synonym_id, reason="Wrong", actor="lead")
    assert use_cases.resolve_term("goods and services tax", dataset="original", config_path=config).status == "unknown"


def test_exports_approved_synonyms_to_a_new_synonym_set_file(isolated_db: Path, config: Path, tmp_path: Path) -> None:
    from taxonomy_pipeline.taxonomy.synonym_set import SynonymSetSource

    kept = use_cases.add_synonym(_request(), config_path=config)
    removed = use_cases.add_synonym(_request(term="GST levy"), config_path=config)
    use_cases.remove_synonym(removed.synonym_id, reason="Wrong", actor="lead")
    path = tmp_path / "synonyms_v1.json"
    exported = use_cases.export_synonyms("original", path)
    assert [s.synonym_id for s in exported.synonyms] == [kept.synonym_id]
    assert SynonymSetSource("synonyms_v1", path).load().synonyms == exported.synonyms
    with pytest.raises(ValueError, match="already exists"):
        use_cases.export_synonyms("original", path)


def test_cli_resolves_and_exports(isolated_db: Path, config: Path, tmp_path: Path,
                                  capsys: pytest.CaptureFixture[str]) -> None:
    use_cases.add_synonym(_request(), config_path=config)
    assert main(["synonym-resolve", "Goods and Services Tax", "--config", str(config)]) == 0
    assert '"via": "synonym"' in capsys.readouterr().out
    assert main(["synonyms", "--export", str(tmp_path / "s.json")]) == 1
    assert "--export needs --dataset" in capsys.readouterr().err
    assert main(["synonyms", "--export", str(tmp_path / "s.json"), "--dataset", "original"]) == 0
    assert "wrote 1 approved synonyms" in capsys.readouterr().out


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


def test_synonym_check_lists_synonyms_stored_before_the_ambiguity_rules(
    isolated_db: Path, config: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    from taxonomy_pipeline.synonyms import add_synonym as store_add

    for concept_id, name in (("101", "gst"), ("102", "unused-tag")):  # what #59 allowed: one term, two concepts
        store_add(_request(concept_id=concept_id, term="Levy"), canonical_name=name, snapshot_id="s")
    problems = use_cases.synonym_problems("original", config_path=config)
    assert sorted((p.concept_id, p.problem) for p in problems) == [
        ("101", "ambiguous: a global synonym of 2 concepts"), ("102", "ambiguous: a global synonym of 2 concepts"),
    ]
    assert main(["synonym-check", "--config", str(config)]) == 1
    assert "[FLAGGED] 2 synonym problem(s)" in capsys.readouterr().out


def test_adding_checks_only_against_synonyms_that_still_fit(isolated_db: Path, config: Path) -> None:
    from taxonomy_pipeline.synonyms import add_synonym as store_add

    # A synonym recorded for tag 102 under a name it no longer has doesn't block "Levy" for tag 101.
    store_add(_request(concept_id="102", term="Levy"), canonical_name="old name", snapshot_id="s")
    assert use_cases.add_synonym(_request(term="Levy"), config_path=config).concept_id == "101"
