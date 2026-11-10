"""Synonyms are stored beside the taxonomy, never in it, and every change is in the audit log."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from taxonomy_pipeline.audit import ChangeQuery, read_changes
from taxonomy_pipeline.synonyms import (
    DuplicateSynonym,
    RetiredConcept,
    Synonym,
    SynonymCreate,
    SynonymError,
    SynonymNotFound,
    SynonymQuery,
    UnknownConcept,
    add_synonym,
    concept_name,
    list_synonyms,
    remove_synonym,
    store,
)
from taxonomy_pipeline.taxonomy.base import TaxonomyDataset

TAXONOMY = TaxonomyDataset(
    name="original", services=[{"id": 1, "name": "Industry"}],
    categories=[{"id": 1, "name": "Computing", "service_id": 1}],
    specialities=[{"id": 10, "name": "Web Development", "category_id": 1}],
    tags=[{"id": 100, "name": "JavaScript"}, {"id": 101, "name": "Python"}],
    speciality_tags=[{"id": 1000, "speciality_id": 10, "tag_id": 100}],
)


def _request(term: str = "JS", concept_id: str = "100", **extra: str) -> SynonymCreate:
    return SynonymCreate(dataset="original", concept_level="tags", concept_id=concept_id, term=term,
                         reason="Common abbreviation", actor="reviewer", **extra)


def _add(term: str = "JS", concept_id: str = "100") -> Synonym:
    name = concept_name(TAXONOMY, "tags", concept_id)
    return add_synonym(_request(term, concept_id), canonical_name=name, snapshot_id="sha256:snapshot")


def test_several_synonyms_map_to_one_concept_and_keep_the_canonical_name_separate(isolated_db: Path) -> None:
    js, ecmascript = _add("JS"), _add("ECMAScript")
    listed = list_synonyms(SynonymQuery(concept_id="100")).synonyms
    assert [s.synonym_id for s in listed] == [js.synonym_id, ecmascript.synonym_id]
    assert {(s.term, s.concept_name) for s in listed} == {("JS", "JavaScript"), ("ECMAScript", "JavaScript")}
    assert js.normalized_term == "js" and js.status == "approved"


def test_adding_a_synonym_never_writes_a_taxonomy_row(isolated_db: Path) -> None:
    _add()
    with closing(sqlite3.connect(isolated_db)) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"taxonomy_synonyms", "taxonomy_change_log"} <= tables
    assert not tables & {"tags", "categories", "specialities", "speciality_tags"}
    assert TAXONOMY.tags == [{"id": 100, "name": "JavaScript"}, {"id": 101, "name": "Python"}]


@pytest.mark.parametrize("duplicate", ["JS", "js", " JS. "])
def test_the_same_normalised_term_for_the_same_concept_is_rejected(isolated_db: Path, duplicate: str) -> None:
    first = _add("JS")
    with pytest.raises(DuplicateSynonym, match=first.synonym_id):
        _add(duplicate)
    assert list_synonyms(SynonymQuery()).total == 1
    assert read_changes(ChangeQuery(action="synonym_add")).total == 1


def test_one_term_can_be_a_synonym_of_two_different_concepts(isolated_db: Path) -> None:
    # Ambiguous terms are #61's concern; storage only rejects exact repeats of one mapping.
    _add("Script", "100")
    _add("Script", "101")
    assert list_synonyms(SynonymQuery(term="script")).total == 2


@pytest.mark.parametrize("term", ["JavaScript", "javascript.", "  JAVASCRIPT  "])
def test_a_term_equal_to_the_concepts_own_name_is_refused(isolated_db: Path, term: str) -> None:
    with pytest.raises(SynonymError, match="own name"):
        _add(term)


def test_a_term_with_nothing_left_after_normalising_is_refused(isolated_db: Path) -> None:
    with pytest.raises(SynonymError, match="no letters or digits"):
        _add("...")


def test_an_unknown_concept_is_refused_before_anything_is_stored(isolated_db: Path) -> None:
    with pytest.raises(UnknownConcept, match="no tag with ID 999"):
        concept_name(TAXONOMY, "tags", "999")
    assert not isolated_db.exists()


def test_add_and_remove_are_logged_with_who_and_why(isolated_db: Path) -> None:
    synonym = _add()
    removed = remove_synonym(synonym.synonym_id, reason="Too ambiguous", actor="lead")
    events = read_changes(ChangeQuery(concept_ref="tag:100")).changes
    assert [e.action for e in events] == ["synonym_add", "synonym_remove"]
    added, gone = events
    assert (added.actor, added.reason, added.concept_name, added.scope) == (
        "reviewer", "Common abbreviation", "JavaScript", "live")
    assert added.after["term"] == "JS" and added.snapshot_id == "sha256:snapshot"
    assert (gone.actor, gone.reason) == ("lead", "Too ambiguous")
    assert gone.before["status"] == "approved" and gone.after["status"] == "removed"
    assert removed.removed_by == "lead" and removed.removed_at is not None


def test_removed_synonyms_are_kept_hidden_by_default_and_can_be_added_again(isolated_db: Path) -> None:
    first = _add()
    remove_synonym(first.synonym_id, reason="Added by mistake", actor="lead")
    assert list_synonyms(SynonymQuery()).total == 0
    assert [s.status for s in list_synonyms(SynonymQuery(include_removed=True)).synonyms] == ["removed"]
    again = _add()
    assert again.synonym_id != first.synonym_id
    assert list_synonyms(SynonymQuery()).synonyms == [again]


def test_removing_an_unknown_or_already_removed_synonym_fails(isolated_db: Path) -> None:
    synonym = _add()
    remove_synonym(synonym.synonym_id, reason="Wrong", actor="lead")
    for synonym_id in (synonym.synonym_id, "missing"):
        with pytest.raises(SynonymNotFound):
            remove_synonym(synonym_id, reason="Again", actor="lead")
    assert read_changes(ChangeQuery(action="synonym_remove")).total == 1


def test_a_failed_audit_write_rolls_back_the_synonym(isolated_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_log(*args, **kwargs):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(store, "log_change", broken_log)
    with pytest.raises(sqlite3.OperationalError):
        _add()
    assert list_synonyms(SynonymQuery(include_removed=True)).total == 0


def test_listing_reads_without_creating_the_database(isolated_db: Path) -> None:
    assert list_synonyms(SynonymQuery()).total == 0
    assert not isolated_db.exists()


def test_a_database_from_another_schema_version_is_refused(isolated_db: Path) -> None:
    _add()
    with closing(sqlite3.connect(isolated_db)) as connection, connection:
        connection.execute("UPDATE taxonomy_synonym_schema SET version = 99")
    with pytest.raises(SynonymError, match="schema version 99"):
        _add("ECMAScript")


@pytest.mark.parametrize("term", ["#", "--", "()", "...-", "test", "New"])
def test_symbol_only_and_placeholder_terms_are_refused(isolated_db: Path, term: str) -> None:
    with pytest.raises(SynonymError, match="no letters or digits, or is a placeholder"):
        _add(term)
    assert not isolated_db.exists()


@pytest.mark.parametrize("term", ["Javascripts", "JAVASCRIPT."])
def test_a_spelling_of_the_concepts_own_name_is_refused(isolated_db: Path, term: str) -> None:
    with pytest.raises(SynonymError, match="own name"):
        _add(term)


@pytest.mark.parametrize(("first", "second"), [("JS tag", "JS tags"), ("JS & TS", "JS and TS")])
def test_spelling_variants_of_an_approved_synonym_are_duplicates(isolated_db: Path, first: str, second: str) -> None:
    original = _add(first)
    with pytest.raises(DuplicateSynonym, match=original.synonym_id):
        _add(second)


def test_a_retired_concept_is_refused() -> None:
    retired = TAXONOMY.model_copy(update={"tags": [{"id": 100, "name": "JavaScript", "status": "0"}]})
    with pytest.raises(RetiredConcept, match="retired"):
        concept_name(retired, "tags", "100")


def test_a_repeated_id_resolves_to_its_last_row_like_hierarchy() -> None:
    repeated = TAXONOMY.model_copy(update={"tags": [{"id": 100, "name": "Old"}, {"id": 100, "name": "JavaScript"}]})
    assert concept_name(repeated, "tags", "100") == "JavaScript"


def test_a_dataset_name_now_pointing_at_another_taxonomy_is_refused(isolated_db: Path) -> None:
    _add("JS")  # recorded against tag 100 "JavaScript"
    with pytest.raises(SynonymError, match="now points at a different taxonomy"):
        add_synonym(_request("Rb"), canonical_name="Ruby", snapshot_id="other")
    assert list_synonyms(SynonymQuery()).total == 1


def test_a_dropped_table_or_index_is_recreated_on_the_next_write(isolated_db: Path) -> None:
    _add("JS")
    with closing(sqlite3.connect(isolated_db)) as connection, connection:
        connection.execute("DROP INDEX synonym_approved_mapping")
    _add("ECMAScript")
    with pytest.raises(DuplicateSynonym):
        _add("JS")
    with closing(sqlite3.connect(isolated_db)) as connection:
        indexes = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "synonym_approved_mapping" in indexes
