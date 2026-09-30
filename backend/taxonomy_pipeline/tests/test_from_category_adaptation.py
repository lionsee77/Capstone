from taxonomy_pipeline.classification.from_category_adaptation import build_classification_outputs
from taxonomy_pipeline.classification.models import CLASSIFICATION_OUTPUTS


def _base(**overrides):
    placement = {
        "tag_id": 9001,
        "tag_name": "loan processing",
        "source_category_id": 501,
        "source_category_name": "Banking",
        "action": "SKIP_DUPLICATE",
        "category_match_type": "exact",
        "category_score": 100.0,
        "category_semantic_score": None,
        "target_category_id": 1,
        "target_category_name": "Banking",
        "needs_review": False,
    }
    placement.update(overrides)
    return placement


def test_matched_category_and_speciality_resolves_fully():
    placement = _base(
        speciality_action="SKIP_SEMANTIC_DUPLICATE",
        speciality_match_type="semantic",
        speciality_score=None,
        speciality_semantic_score=0.82,
        target_speciality_id=1,
        target_speciality_name="Retail Banking",
    )
    records = build_classification_outputs([placement], "skillsfuture")

    assert len(records) == 1
    record = records[0]
    assert record.source_dataset == "skillsfuture"
    assert record.tag_id == "9001"
    assert record.original_tag == "loan processing"
    assert record.original_classification == "classified"
    assert record.category == "Banking"
    assert record.speciality == "Retail Banking"
    assert record.confidence == 0.82  # min(1.0 exact category, 0.82 semantic speciality)
    assert "Banking" in record.reason and "Retail Banking" in record.reason


def test_new_category_and_new_speciality_are_proposed_with_conservative_confidence():
    placement = _base(
        action="NEW_CATEGORY",
        category_match_type=None,
        category_score=None,
        target_category_id="skillsfuture:501",
        target_category_name="Banking",
        speciality_action="NEW_SPECIALITY",
        speciality_match_type=None,
        speciality_score=None,
        speciality_semantic_score=None,
        target_speciality_id="skillsfuture:601",
        target_speciality_name="Retail Banking",
        service_id=1,
    )
    records = build_classification_outputs([placement], "skillsfuture")

    record = records[0]
    assert record.category == "Banking"
    assert record.speciality == "Retail Banking"
    assert record.confidence == 0.4


def test_review_ambiguous_category_is_fully_unresolved():
    placement = {
        "tag_id": 9002, "tag_name": "rocket fuel", "source_category_id": 502,
        "source_category_name": "Space Exploration", "action": "REVIEW_AMBIGUOUS",
        "closest_baseline_name": "Banking", "closest_baseline_score": 41.2,
        "target_category_id": None, "target_category_name": None, "needs_review": True,
    }
    records = build_classification_outputs([placement], "skillsfuture")

    record = records[0]
    assert (record.category, record.speciality, record.confidence) == (None, None, None)
    assert "Space Exploration" in record.reason and "Banking" in record.reason


def test_matched_category_with_unresolved_speciality_nulls_the_whole_record():
    # Category is confidently known, but nothing could be said about a
    # speciality - the contract's all-or-null rule means the category can't
    # be reported alone either.
    placement = _base(speciality_action="REVIEW_AMBIGUOUS")
    records = build_classification_outputs([placement], "skillsfuture")

    record = records[0]
    assert (record.category, record.speciality, record.confidence) == (None, None, None)
    assert "No speciality could be resolved" in record.reason


def test_no_source_category_is_unresolved_and_still_classified():
    placement = {
        "tag_id": 9003, "tag_name": "unlinked tag", "action": "NO_SOURCE_CATEGORY", "needs_review": True,
    }
    records = build_classification_outputs([placement], "skillsfuture")

    record = records[0]
    assert record.original_classification == "classified"
    assert (record.category, record.speciality, record.confidence) == (None, None, None)
    assert "Case B" in record.reason


def test_output_validates_against_the_shared_contract():
    resolved = _base(
        speciality_action="SKIP_DUPLICATE", speciality_match_type="exact", speciality_score=100.0,
        speciality_semantic_score=None, target_speciality_id=1, target_speciality_name="Retail Banking",
    )
    unresolved = {
        "tag_id": 9002, "tag_name": "rocket fuel", "action": "REVIEW_AMBIGUOUS",
        "closest_baseline_name": None, "closest_baseline_score": None,
        "target_category_id": None, "target_category_name": None, "needs_review": True,
    }
    records = build_classification_outputs([resolved, unresolved], "skillsfuture")

    # Round-trips through the exact validator Bryan's side uses.
    validated = CLASSIFICATION_OUTPUTS.validate_json(CLASSIFICATION_OUTPUTS.dump_json(records))
    assert len(validated) == 2
