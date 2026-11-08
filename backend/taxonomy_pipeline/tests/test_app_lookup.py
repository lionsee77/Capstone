"""The lookup use case: choosing a snapshot, and reporting matches with where each concept sits."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest

from taxonomy_pipeline.app import lookup
from taxonomy_pipeline.matching.semantic import SemanticMatcher
from tests.conftest import FIXTURES

ADDED_TAG = "INSERT INTO `tags` (`id`, `name`, `status`) VALUES (200, '{name}', '{status}');\n"


@pytest.fixture
def round_with(integration_round: Callable[..., Path], tmp_path: Path) -> Callable[..., Path]:
    """Write a round over the mini taxonomy that adds one tag; returns the integrations root."""
    baseline = (FIXTURES / "mini_original.sql").read_text(encoding="utf-8")

    def write(name: str, tag: str = "Payroll", *, status: str = "1", **kwargs: object) -> Path:
        additions = ADDED_TAG.format(name=tag, status=status)
        integration_round(name, {"mini.sql": baseline}, additions, **kwargs)
        return tmp_path / "integrations"

    return write


def test_reports_an_existing_term_with_its_level_and_full_path(round_with: Callable[..., Path]) -> None:
    result = lookup.lookup_term("tax advisory", round_with("r1"))

    assert result.exists
    assert result.snapshot == "r1"
    assert result.near == []
    [match] = result.exact
    assert (match.id, match.name, match.level, match.match_type) == (10, "Tax Advisory", "specialities", "exact")
    [path] = match.paths
    assert (path.service.name, path.category.name, path.speciality.name) == (
        "Professional Services", "Accounting", "Tax Advisory",
    )


def test_reports_a_term_nothing_resembles_as_not_existing(round_with: Callable[..., Path]) -> None:
    result = lookup.lookup_term("Underwater Welding", round_with("r1"))

    assert not result.exists
    assert result.exact == [] and result.near == []


def test_a_misspelling_is_near_rather_than_existing(round_with: Callable[..., Path]) -> None:
    result = lookup.lookup_term("Brand Stratgy", round_with("r1"))

    assert not result.exists
    assert [match.name for match in result.near] == ["Brand Strategy"]


def test_a_tag_gets_one_path_per_speciality_it_is_linked_to(round_with: Callable[..., Path]) -> None:
    [match] = lookup.lookup_term("advisory", round_with("r1"), level="tags").exact

    assert {path.speciality.name for path in match.paths} == {"Tax Advisory", "Brand Strategy"}


def test_reads_the_rows_the_round_added_on_top_of_its_baseline(round_with: Callable[..., Path]) -> None:
    assert lookup.lookup_term("payroll", round_with("r1")).exists


def test_the_newest_round_answers_unless_another_is_named(round_with: Callable[..., Path]) -> None:
    round_with("older", "Payroll", created_at="2026-10-01T00:00:00+00:00")
    root = round_with("a-newer", "Bookkeeping", created_at="2026-11-01T00:00:00+00:00")

    newest = lookup.lookup_term("bookkeeping", root)
    named = lookup.lookup_term("payroll", root, snapshot="older")

    assert (newest.snapshot, newest.exists) == ("a-newer", True)
    assert (named.snapshot, named.exists) == ("older", True)
    assert not lookup.lookup_term("payroll", root).exists


def test_a_retired_row_still_counts_as_existing_but_is_marked(round_with: Callable[..., Path]) -> None:
    result = lookup.lookup_term("payroll", round_with("r1", status="0"))

    assert result.exists
    assert not result.exact[0].active


def test_a_row_with_no_status_column_is_active(round_with: Callable[..., Path]) -> None:
    assert lookup.lookup_term("gst", round_with("r1")).exact[0].active


def test_the_level_filter_applies_to_exact_and_near_matches(round_with: Callable[..., Path]) -> None:
    root = round_with("r1")

    assert {match.level for match in lookup.lookup_term("accounting", root, level="categories").exact} == {"categories"}
    assert not lookup.lookup_term("accounting", root, level="tags").exists


def test_the_limit_caps_near_matches_but_never_exact_ones(round_with: Callable[..., Path]) -> None:
    root = round_with("r1")

    assert len(lookup.lookup_term("Accounting", root, limit=1).exact) == 2
    assert len(lookup.lookup_term("Accountng", root, limit=1).near) == 1
    assert len(lookup.lookup_term("Accountng", root, limit=5).near) == 2


def test_meaning_matching_uses_the_shared_matcher_only_when_asked(
    round_with: Callable[..., Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Matcher:
        def similarity(self, queries: list[str], choices: list[str]) -> np.ndarray:
            return np.array([[0.9 if choice == "Marketing" else 0.0 for choice in choices]])

        def retain(self, names: list[str]) -> None:
            pass

    monkeypatch.setattr(lookup, "_semantic_matcher", Matcher)
    root = round_with("r1")

    assert lookup.lookup_term("Advertising", root).near == []
    [match] = lookup.lookup_term("Advertising", root, semantic=True).near
    assert (match.name, match.match_type, match.semantic_score) == ("Marketing", "semantic", 0.9)


def test_concurrent_meaning_matches_load_and_run_the_model_one_at_a_time(
    round_with: Callable[..., Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two threads inside the real model kill the process, so this checks they never overlap."""
    guard = threading.Lock()
    active, peak = 0, 0

    def in_model() -> None:
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        time.sleep(0.01)
        with guard:
            active -= 1

    class Matcher:
        def similarity(self, queries: list[str], choices: list[str]) -> np.ndarray:
            in_model()
            return np.zeros((len(queries), len(choices)))

        def retain(self, names: list[str]) -> None:
            pass

    def load() -> Matcher:
        in_model()
        return Matcher()

    monkeypatch.setattr(lookup, "_load_matcher", load)
    root = round_with("r1")

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda term: lookup.lookup_term(term, root, semantic=True), ["a", "b", "c", "d"]))

    assert len(results) == 4
    assert peak == 1


def test_a_model_that_cannot_load_is_reported_as_unavailable(
    round_with: Callable[..., Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail() -> None:
        raise OSError("no network")

    monkeypatch.setattr(lookup, "_load_matcher", fail)

    with pytest.raises(lookup.SemanticUnavailable, match="no network"):
        lookup.lookup_term("tax", round_with("r1"), semantic=True)


def test_no_rounds_at_all_is_its_own_error(tmp_path: Path) -> None:
    with pytest.raises(lookup.NoSnapshots):
        lookup.lookup_term("tax", tmp_path / "missing")


def test_an_unknown_snapshot_name_is_not_found(round_with: Callable[..., Path]) -> None:
    root = round_with("r1")

    with pytest.raises(lookup.SnapshotNotFound):
        lookup.lookup_term("tax", root, snapshot="nope")
    with pytest.raises(lookup.SnapshotNotFound):
        lookup.lookup_term("tax", root, snapshot="../r1")


def test_a_changed_snapshot_is_picked_up_without_a_restart(round_with: Callable[..., Path]) -> None:
    root = round_with("r1", "Payroll")
    assert lookup.lookup_term("payroll", root).exists

    (root / "r1" / "additions.sql").write_text(ADDED_TAG.format(name="Invoicing", status="1"), encoding="utf-8")

    assert not lookup.lookup_term("payroll", root).exists
    assert lookup.lookup_term("invoicing", root).exists


@pytest.mark.parametrize(
    ("term", "options"),
    [
        ("", {}), ("   ", {}), ("!?-", {}), ("x" * 201, {}),
        ("tax", {"limit": 0}), ("tax", {"limit": 101}),
        ("tax", {"min_score": -1}), ("tax", {"min_score": 101}),
        ("tax", {"level": "industries"}),
    ],
)
def test_invalid_input_is_rejected_before_anything_loads(term: str, options: dict, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        lookup.lookup_term(term, tmp_path / "missing", **options)


def test_a_snapshot_built_on_renamed_tables_is_still_searched(
    integration_round: Callable[..., Path], tmp_path: Path,
) -> None:
    """`integrate` keeps the dump's table names and records the mapping only in `enriched.yaml`."""
    directory = integration_round(
        "renamed",
        {"mini.sql": "INSERT INTO `sf_tags` (`id`, `name`) VALUES (100, 'Payroll');\n"},
        "INSERT INTO `sf_tags` (`id`, `name`) VALUES (200, 'Invoicing');\n",
    )
    (directory / "enriched.yaml").write_text(
        "datasets:\n- name: enriched\n  type: sql_dump\n  paths: [baseline/mini.sql, additions.sql]\n"
        "  table_map:\n    tags: sf_tags\n"
        "- name: other\n  paths: [elsewhere.sql]\n  table_map:\n    tags: wrong_table\n",
        encoding="utf-8",
    )
    root = tmp_path / "integrations"

    assert lookup.lookup_term("payroll", root).exists
    assert lookup.lookup_term("invoicing", root).exists


def test_only_the_current_snapshots_name_embeddings_are_kept_between_lookups(
    round_with: Callable[..., Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Model:
        def encode(self, names: list[str], normalize_embeddings: bool) -> list[np.ndarray]:
            return [np.ones(2) for _ in names]

    matcher = SemanticMatcher.__new__(SemanticMatcher)
    matcher.model, matcher._cache = Model(), {}
    monkeypatch.setattr(lookup, "_load_matcher", lambda: matcher)
    round_with("older", "Payroll", created_at="2026-10-01T00:00:00+00:00")
    root = round_with("newer", "Bookkeeping", created_at="2026-11-01T00:00:00+00:00")

    lookup.lookup_term("first search", root, semantic=True, snapshot="older")
    assert "first search" not in matcher._cache
    assert "Payroll" in matcher._cache

    lookup.lookup_term("second search", root, semantic=True)
    assert "second search" not in matcher._cache
    assert "Payroll" not in matcher._cache
    assert "Bookkeeping" in matcher._cache
