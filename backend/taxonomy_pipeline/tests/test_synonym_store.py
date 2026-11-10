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


def test_the_same_term_can_be_global_and_scoped_for_one_concept(isolated_db: Path) -> None:
    _add()
    scoped = add_synonym(_request(scope_level="categories", scope_id="1"), canonical_name="JavaScript",
                         snapshot_id="sha256:snapshot")
    assert scoped.scope == ("categories", "1")
    with pytest.raises(DuplicateSynonym):
        add_synonym(_request(scope_level="categories", scope_id="1"), canonical_name="JavaScript",
                    snapshot_id="sha256:snapshot")
    assert [s.scope for s in list_synonyms(SynonymQuery()).synonyms] == [None, ("categories", "1")]


def test_a_refusing_check_sees_only_the_approved_synonyms_with_its_term_and_stores_nothing(
    isolated_db: Path,
) -> None:
    first = _add("JS")
    _add("ECMAScript")
    seen: list[Synonym] = []

    def refuse(approved: list[Synonym]) -> None:
        seen.extend(approved)
        raise SynonymError("refused by check")

    with pytest.raises(SynonymError, match="refused by check"):
        add_synonym(_request("js.", "101"), canonical_name="Python", snapshot_id="s", check=refuse)
    assert seen == [first]
    assert list_synonyms(SynonymQuery()).total == 2


def _version_1_table(path: Path) -> None:
    """#59's table, with one approved synonym, as a database upgraded from that PR has it."""
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("""CREATE TABLE taxonomy_synonyms (synonym_id TEXT PRIMARY KEY, term TEXT NOT NULL,
            normalized_term TEXT NOT NULL, term_key TEXT NOT NULL, dataset TEXT NOT NULL,
            concept_level TEXT NOT NULL, concept_id TEXT NOT NULL, concept_name TEXT NOT NULL,
            snapshot_id TEXT NOT NULL, status TEXT NOT NULL, source TEXT NOT NULL, reason TEXT NOT NULL,
            actor TEXT NOT NULL, created_at TEXT NOT NULL, removed_at TEXT, removed_by TEXT, removal_reason TEXT)""")
        connection.execute("""CREATE UNIQUE INDEX synonym_approved_mapping ON taxonomy_synonyms(dataset,
            concept_level, concept_id, term_key) WHERE status = 'approved'""")
        connection.execute("CREATE TABLE taxonomy_synonym_schema (singleton INTEGER PRIMARY KEY, version INTEGER)")
        connection.execute("INSERT INTO taxonomy_synonym_schema VALUES (1, 1)")
        connection.execute("""INSERT INTO taxonomy_synonyms VALUES ('old', 'JS', 'js', 'js', 'original', 'tags',
            '100', 'JavaScript', 's', 'approved', 'manual', 'From #59', 'reviewer', '2026-11-01T00:00:00+00:00',
            NULL, NULL, NULL)""")


def test_a_version_1_table_reads_as_global_synonyms_before_anything_migrates_it(isolated_db: Path) -> None:
    _version_1_table(isolated_db)
    assert [(s.synonym_id, s.scope) for s in list_synonyms(SynonymQuery()).synonyms] == [("old", None)]
    with closing(sqlite3.connect(isolated_db)) as connection:
        assert connection.execute("SELECT version FROM taxonomy_synonym_schema").fetchone() == (1,)


def test_a_version_1_table_is_migrated_on_write_and_its_synonyms_become_global(isolated_db: Path) -> None:
    _version_1_table(isolated_db)
    add_synonym(_request(scope_level="categories", scope_id="1"), canonical_name="JavaScript", snapshot_id="s")
    listed = list_synonyms(SynonymQuery()).synonyms
    assert [(s.synonym_id, s.scope) for s in listed] == [("old", None), (listed[1].synonym_id, ("categories", "1"))]
    with closing(sqlite3.connect(isolated_db)) as connection:
        assert connection.execute("SELECT version FROM taxonomy_synonym_schema").fetchone() == (store.SCHEMA_VERSION,)


def _propose(*terms: str) -> tuple[list[Synonym], dict[str, int]]:
    from taxonomy_pipeline.synonyms import propose_synonyms

    return propose_synonyms("original", [(_request(term), "JavaScript") for term in terms], snapshot_id="s")


def _approve(synonym_id: str, *, name: str = "JavaScript", snapshot: str = "s", **kwargs: str) -> Synonym:
    from taxonomy_pipeline.synonyms import review_synonym

    return review_synonym(synonym_id, approve=True, reason=kwargs.get("reason", "Common abbreviation"),
                          actor=kwargs.get("actor", "lead"), canonical_name=name, snapshot_id=snapshot)


def test_suggestions_are_stored_as_proposed_without_resolving_or_auditing(isolated_db: Path) -> None:
    proposed, skipped = _propose("JS", "javascript", "JS", "ECMAScript", "#", "JS.")
    assert [s.term for s in proposed] == ["JS", "ECMAScript"] and all(s.status == "proposed" for s in proposed)
    assert skipped == {"same_as_name_or_invalid": 2, "already_known": 2}
    assert list_synonyms(SynonymQuery()).total == 0
    assert list_synonyms(SynonymQuery(status="proposed")).total == 2
    assert read_changes(ChangeQuery()).total == 0


def test_approving_a_suggestion_makes_it_resolve_and_logs_it_as_an_addition(isolated_db: Path) -> None:
    [js] = _propose("JS")[0]
    approved = _approve(js.synonym_id)
    assert (approved.status, approved.reviewed_by, approved.review_reason) == (
        "approved", "lead", "Common abbreviation")
    assert list_synonyms(SynonymQuery()).synonyms == [approved]
    [event] = read_changes(ChangeQuery()).changes
    assert (event.action, event.actor, event.reason, event.before["status"]) == (
        "synonym_add", "lead", "Common abbreviation", "proposed")


def test_rejecting_a_suggestion_keeps_it_and_stops_it_being_proposed_again(isolated_db: Path) -> None:
    from taxonomy_pipeline.synonyms import review_synonym

    [js] = _propose("JS")[0]
    rejected = review_synonym(js.synonym_id, approve=False, reason="Too vague", actor="lead")
    assert rejected.status == "rejected"
    [event] = read_changes(ChangeQuery()).changes
    assert (event.action, event.scope, event.decision["action"]) == ("review_decision", "review", "reject")
    assert _propose("JS") == ([], {"already_known": 1})
    with pytest.raises(SynonymNotFound, match="no proposed synonym"):
        _approve(js.synonym_id, reason="Changed my mind")


def test_approving_a_suggestion_already_added_by_hand_is_refused(isolated_db: Path) -> None:
    [js] = _propose("JS")[0]
    _add("JS")
    with pytest.raises(DuplicateSynonym):
        _approve(js.synonym_id)
    assert list_synonyms(SynonymQuery(status="proposed")).total == 1


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


def test_approval_records_the_concepts_current_name_and_snapshot_and_refuses_its_own_name(isolated_db: Path) -> None:
    [js, ecma] = _propose("JS", "ECMAScript")[0]
    approved = _approve(js.synonym_id, name="JavaScript (ECMAScript)", snapshot="sha256:now")
    assert (approved.concept_name, approved.snapshot_id) == ("JavaScript (ECMAScript)", "sha256:now")
    [event] = read_changes(ChangeQuery()).changes
    assert (event.concept_name, event.snapshot_id) == ("JavaScript (ECMAScript)", "sha256:now")
    with pytest.raises(SynonymError, match="own name"):
        _approve(ecma.synonym_id, name="ecmascript")  # the concept was renamed to the suggested term


def test_a_removed_synonym_is_not_proposed_again(isolated_db: Path) -> None:
    [js] = _propose("JS")[0]
    remove_synonym(_approve(js.synonym_id).synonym_id, reason="Wrong", actor="lead")
    assert _propose("JS") == ([], {"already_known": 1})


def test_a_version_2_table_reads_before_anything_migrates_it(isolated_db: Path) -> None:
    _version_1_table(isolated_db)
    with closing(sqlite3.connect(isolated_db)) as connection, connection:  # what #134's migration leaves
        connection.execute("ALTER TABLE taxonomy_synonyms ADD COLUMN scope_level TEXT NOT NULL DEFAULT ''")
        connection.execute("ALTER TABLE taxonomy_synonyms ADD COLUMN scope_id TEXT NOT NULL DEFAULT ''")
        connection.execute("UPDATE taxonomy_synonym_schema SET version = 2")
    from taxonomy_pipeline.synonyms import approved_synonyms, get_synonym

    assert [s.synonym_id for s in list_synonyms(SynonymQuery()).synonyms] == ["old"]
    assert get_synonym("old").reviewed_by is None
    assert [s.synonym_id for s in approved_synonyms("original")] == ["old"]
