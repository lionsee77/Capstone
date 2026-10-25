"""Check identity-preserving deduplication and optional matching without model downloads."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from taxonomy_pipeline.enrichment.deduplication import deduplicate_outputs
from taxonomy_pipeline.enrichment.models import BatchValidationReport, DeduplicationReport
from taxonomy_pipeline.enrichment.validation import validate_outputs
from taxonomy_pipeline.taxonomy.base import TaxonomyDataset
from taxonomy_pipeline.taxonomy.sql_dump import SqlDumpSource

FIXTURES = Path(__file__).parent / "fixtures" / "classification"


# Load a small independent baseline for each test.
def taxonomy() -> TaxonomyDataset:
    return TaxonomyDataset.model_validate_json((FIXTURES / "taxonomy.json").read_text(encoding="utf-8"))


# Supply complete proposed placements with controllable identities, names, and parents.
def placement(identifier: str, tag: str, **changes: Any) -> dict[str, Any]:
    return dict(
        source_dataset="source", tag_id=identifier, tag=tag, original_classification="classified",
        service="Industry", category="New category", speciality="New speciality", confidence=1.0,
        reason="Synthetic proposal.",
    ) | changes


# Validate arrays through the real intake boundary before exercising deduplication.
def intake(rows: list[dict[str, Any]]) -> BatchValidationReport:
    return validate_outputs({"rows": json.dumps(rows)}, taxonomy())


class FakeMatcher:
    """Supply deterministic name similarities through the shared matcher's existing interface."""

    # Store selected pair scores without loading an embedding model.
    def __init__(self, scores: dict[tuple[str, str], float] | None = None) -> None:
        self.scores = scores or {}

    # Return a symmetric cosine matrix, including identical-name evidence.
    def similarity(self, queries: list[str], choices: list[str]) -> np.ndarray:
        return np.array([
            [1.0 if left == right else self.scores.get((left, right), self.scores.get((right, left), 0.0))
             for right in choices] for left in queries
        ])


# Collect matches within both branches and across them without collapsing dataset identities.
def test_matches_cover_both_branches_and_preserve_all_placements() -> None:
    validation = validate_outputs({
        name: (FIXTURES / name).read_text(encoding="utf-8") for name in ("case_a.json", "case_b.json")
    }, taxonomy())
    before = validation.model_dump_json()
    result = deduplicate_outputs(validation)
    assert result.counts["source_tags"] == 7
    assert result.counts["unique_placements"] == 8
    assert result.counts["received_records"] == 8
    pairs = {(pair.left.source_dataset, pair.right.source_dataset) for pair in result.candidates}
    assert ("categorized-skills", "categorized-skills") in pairs
    assert ("plain-skills", "plain-skills") in pairs
    assert ("categorized-skills", "plain-skills") in pairs
    assert len(next(tag for tag in result.tags if tag.source.source_dataset == "categorized-skills"
                    and tag.source.tag_id == "42").placements) == 2
    assert len({node.path[1] for node in result.hierarchy if node.level == "category"}) == 2
    assert validation.model_dump_json() == before
    assert not result.complete
    assert "semantic" in result.skipped_checks
    assert DeduplicationReport.model_validate_json(result.model_dump_json()) == result


# Identical names with different meanings remain separate even at maximum confidence and similarity.
def test_same_name_different_concepts_are_candidates_not_automatic_merges() -> None:
    result = deduplicate_outputs(intake([
        placement("1", "Python", speciality="Programming"), placement("2", "Python", speciality="Zoology"),
    ]), semantic_matcher=FakeMatcher())
    assert len(result.tags) == 2
    assert len(result.candidates) == 1
    assert "normalized_name" in result.candidates[0].match_types
    assert result.candidates[0].shared_paths == []
    assert result.complete


# Multiple placements of one source tag never become self-matches.
def test_one_source_tag_is_compared_once_and_retains_duplicate_evidence() -> None:
    rows = [placement("1", "SQL"), placement("1", "SQL", confidence=0.5),
            placement("1", "SQL", speciality="Another speciality")]
    result = deduplicate_outputs(intake(rows))
    assert result.counts["source_tags"] == 1
    assert result.counts["unique_placements"] == 2
    assert result.counts["included_records"] == 3
    assert result.candidates == []
    assert len(result.tags[0].origins) == 3
    assert {row.confidence for row in result.tags[0].placements} == {0.5, 1.0}
    assert result.complete  # No distinct tags need a semantic comparison.


# Equal source IDs in different datasets remain separate identities.
def test_same_id_across_datasets_does_not_merge_tags() -> None:
    result = deduplicate_outputs(intake([
        placement("1", "SQL", source_dataset="a"), placement("1", "SQL", source_dataset="b"),
    ]))
    assert len(result.tags) == 2
    assert result.candidates[0].left.source_dataset != result.candidates[0].right.source_dataset


# Fold only identical normalized hierarchy paths and keep label variants and every origin.
def test_hierarchy_groups_keep_parent_boundaries_and_label_variants() -> None:
    rows = [
        placement("1", "First"),
        placement("2", "Second", category=" NEW category! ", speciality="new speciality!"),
        placement("3", "Third", service="Subject"),
        placement("4", "Fourth", category="Another category"),
    ]
    result = deduplicate_outputs(intake(rows))
    assert result.counts["new_category_nodes"] == 3
    assert result.counts["new_speciality_nodes"] == 3
    shared = next(node for node in result.hierarchy if node.path == ["Industry", "new category", "new speciality"])
    assert len(shared.variants) == 2
    assert len(shared.members) == len(shared.origins) == 2
    assert shared.existing_id is None


# Preserve validated existing node IDs in the consolidated hierarchy inventory.
def test_existing_nodes_are_reused_only_at_their_validated_paths() -> None:
    validation = validate_outputs({"a": (FIXTURES / "case_a.json").read_text(encoding="utf-8")}, taxonomy())
    result = deduplicate_outputs(validation)
    assert result.counts["reused_category_nodes"] == 1
    assert result.counts["reused_speciality_nodes"] == 2
    assert {node.existing_id for node in result.hierarchy} == {10, 120, 121}


# Use the saturation engine's spelling folds as evidence rather than silently merging spellings.
def test_shared_match_key_suggests_spelling_variants() -> None:
    result = deduplicate_outputs(intake([
        placement("1", "Data visualisation"), placement("2", "Data visualization"),
    ]))
    assert "match_key" in result.candidates[0].match_types
    assert len(result.tags) == 2


# Lexical matching includes scores exactly at the configured threshold.
def test_lexical_threshold_boundary_is_inclusive() -> None:
    validation = intake([placement("1", "abcdefghij"), placement("2", "abcdefghik")])
    assert len(deduplicate_outputs(validation, lexical_threshold=0.9).candidates) == 1
    assert deduplicate_outputs(validation, lexical_threshold=0.9001).candidates == []


# Semantic matching can find differently worded names without requiring a lexical match.
def test_semantic_only_matches_keep_their_evidence() -> None:
    validation = intake([placement("1", "Charts"), placement("2", "Visual communication")])
    result = deduplicate_outputs(validation, semantic_matcher=FakeMatcher({("charts", "visual communication"): 0.85}))
    assert result.complete
    assert result.candidates[0].match_types == ["semantic"]
    assert result.candidates[0].semantic_score == 0.85


# Pair evidence must not manufacture an unreviewed group through transitive similarity.
def test_similarity_chains_remain_independent_pairs() -> None:
    validation = intake([placement("1", "Alpha"), placement("2", "Beta"), placement("3", "Gamma")])
    matcher = FakeMatcher({("alpha", "beta"): 0.9, ("beta", "gamma"): 0.9})
    result = deduplicate_outputs(validation, lexical_threshold=1, semantic_matcher=matcher)
    assert {(pair.left.tag_id, pair.right.tag_id) for pair in result.candidates} == {("1", "2"), ("2", "3")}
    assert len(result.tags) == 3


@pytest.mark.parametrize("failure", ["exception", "shape", "nan", "range", "asymmetric"])
# Semantic failures remain explicit incomplete checks while exact and lexical candidates survive.
def test_semantic_failure_keeps_name_candidates(failure: str) -> None:
    class BrokenMatcher:
        """Exercise matcher failures without modifying the production engine."""

        # Return a deliberately invalid matrix or raise a simulated loading failure.
        def similarity(self, queries: list[str], choices: list[str]) -> np.ndarray:
            if failure == "exception":
                raise RuntimeError("Model unavailable")
            return {
                "shape": np.zeros((1, 2)), "nan": np.full((2, 2), np.nan),
                "range": np.full((2, 2), 2.0), "asymmetric": np.array([[1, 0.2], [0.9, 1]]),
            }[failure]

    result = deduplicate_outputs(intake([placement("1", "Same"), placement("2", "Same")]),
                                 semantic_matcher=BrokenMatcher())
    assert not result.complete
    assert result.skipped_checks["semantic"].startswith("Semantic matcher failed:")
    assert len(result.candidates) == 1
    assert result.candidates[0].semantic_score is None


# Explain all excluded statuses, including repeated unresolved rows, without matching them as tags.
def test_unresolved_invalid_and_conflicting_rows_are_omitted() -> None:
    validation = intake([
        placement("1", "Pending", service=None, category=None, speciality=None, confidence=None),
        placement("1", "Pending", service=None, category=None, speciality=None, confidence=None),
        placement("2", "Wrong", confidence="bad"),
        placement("3", "First"), placement("3", "Conflict"),
        placement("4", "Kept"),
    ])
    result = deduplicate_outputs(validation)
    assert result.omitted_counts == {"unresolved": 1, "duplicate": 1, "invalid": 1, "review": 2}
    assert result.counts["included_records"] == 1
    assert result.counts["included_records"] + result.counts["omitted_records"] == 6
    assert len(result.tags) == 1


# Read errors must prevent an incomplete source inventory from being reported as fully checked.
def test_unreadable_input_keeps_deduplication_incomplete() -> None:
    validation = validate_outputs({"bad": "[", "good": json.dumps([placement("1", "Only")])}, taxonomy())
    result = deduplicate_outputs(validation)
    assert not result.input_complete
    assert not result.complete
    assert result.counts["source_tags"] == 1


# Empty input requires no pairwise checks and must remain serializable.
def test_empty_input_has_no_candidates() -> None:
    result = deduplicate_outputs(intake([]))
    assert result.complete
    assert result.tags == result.hierarchy == result.candidates == []
    assert result.counts["source_tags"] == 0


@pytest.mark.parametrize("threshold", [-0.01, 1.01, True, "0.9", float("nan"), float("inf")])
@pytest.mark.parametrize("name", ["lexical_threshold", "semantic_threshold"])
# Reject thresholds that could silently skip checks or produce meaningless candidates.
def test_invalid_thresholds_are_rejected(name: str, threshold: Any) -> None:
    with pytest.raises(ValueError):
        deduplicate_outputs(intake([]), **{name: threshold})


# Reordering validated records leaves grouping, evidence ordering, and candidate pairs unchanged.
def test_report_is_deterministic_for_reordered_intake_records() -> None:
    validation = intake([placement("2", "SQL"), placement("1", "Sql"), placement("1", "Sql", confidence=0.4)])
    reordered = validation.model_copy(deep=True)
    reordered.records.reverse()
    assert deduplicate_outputs(validation).model_dump() == deduplicate_outputs(reordered).model_dump()


# Do not silently consolidate hierarchy IDs from conflicting taxonomy resolutions.
def test_conflicting_hierarchy_ids_are_rejected() -> None:
    validation = intake([placement("1", "One"), placement("2", "Two")])
    validation.records[1].target.category_id = 123
    with pytest.raises(ValueError, match="Inconsistent resolved IDs"):
        deduplicate_outputs(validation)


# Exercise SQL loading, intake, and hierarchy grouping together.
def test_sql_intake_integration(tmp_path: Path) -> None:
    path = tmp_path / "taxonomy.sql"
    path.write_text("INSERT INTO services (id,name) VALUES (1,'Industry');\n", encoding="utf-8")
    baseline = SqlDumpSource("integration", [path]).load()
    validation = validate_outputs({"rows": json.dumps([
        placement("42", "Data visualization", category="Data analytics", speciality="Data presentation"),
        placement("48", "Quantum analysis", category="Quantum science", speciality="Quantum computing"),
        placement("49", "Qubit design", category="Quantum science", speciality="Quantum hardware"),
    ])}, baseline)
    result = deduplicate_outputs(validation, semantic_matcher=FakeMatcher())
    assert result.complete
    assert result.counts["source_tags"] == 3
    assert result.counts["new_category_nodes"] == 2  # Data analytics and Quantum science
    assert result.omitted_counts == {}
    assert baseline.categories == baseline.specialities == []
