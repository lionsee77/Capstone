from taxonomy_pipeline.enrichment.from_category_adaptation import build_classification_outputs
from taxonomy_pipeline.enrichment.models import CLASSIFICATION_OUTPUTS


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
        "target_service_id": 1,
        "target_service_name": "Industry",
        "needs_review": False,
    }
    placement.update(overrides)
    return placement


def _content(**overrides):
    placement = {
        "tag_id": 9004,
        "tag_name": "Supplier Sourcing",
        "placement_basis": "content",
        "action": "SKIP_SEMANTIC_DUPLICATE",
        "target_category_id": 2,
        "target_category_name": "Procurement",
        "target_service_id": 2,
        "target_service_name": "Function",
        "speciality_action": "SKIP_SEMANTIC_DUPLICATE",
        "speciality_match_type": "semantic",
        "speciality_score": None,
        "speciality_semantic_score": 0.82,
        "target_speciality_id": 2,
        "target_speciality_name": "Vendor Sourcing",
        "needs_review": False,
    }
    placement.update(overrides)
    return placement


def test_content_match_resolves_to_the_matched_speciality_and_its_own_category():
    records = build_classification_outputs([_content()], "skillsfuture")

    assert len(records) == 1
    record = records[0]
    assert record.source_dataset == "skillsfuture"
    assert record.tag_id == "9004"
    assert record.tag == "Supplier Sourcing"
    assert record.original_classification == "classified"
    assert (record.service, record.category, record.speciality) == ("Function", "Procurement", "Vendor Sourcing")
    assert record.confidence == 0.82  # the match's own evidence; no category step was involved
    assert "Vendor Sourcing" in record.reason and "semantic" in record.reason


def test_fallback_reason_names_the_source_evidence_and_the_closest_speciality_missed():
    placement = _base(
        speciality_action="NEW_SPECIALITY", target_speciality_id="skillsfuture:tag:9001",
        target_speciality_name="loan processing", placement_basis="source_category",
        source_speciality_count=26, source_speciality_sample=["Bank Teller", "Branch Manager", "Credit Analyst"],
        closest_semantic_speciality_name="Retail Banking", closest_semantic_speciality_score=0.44,
    )
    record = build_classification_outputs([placement], "skillsfuture")[0]

    assert record.speciality == "loan processing"
    assert "26 source specialities in 'Banking' (Bank Teller, Branch Manager, Credit Analyst, ...)" in record.reason
    assert "'Retail Banking' in meaning (0.44)" in record.reason


def test_new_category_and_new_speciality_fall_back_to_the_floor():
    # Nothing verified anywhere in this placement - both category and
    # speciality are from-scratch proposals - so confidence sits at the floor.
    placement = _base(
        action="NEW_CATEGORY",
        category_match_type=None,
        category_score=None,
        target_category_id="skillsfuture:501",
        target_category_name="Banking",
        target_service_id=1,
        target_service_name="Industry",
        speciality_action="NEW_SPECIALITY",
        speciality_match_type=None,
        speciality_score=None,
        speciality_semantic_score=None,
        target_speciality_id="skillsfuture:601",
        target_speciality_name="Retail Banking",
    )
    records = build_classification_outputs([placement], "skillsfuture")

    record = records[0]
    assert record.service == "Industry"
    assert record.category == "Banking"
    assert record.speciality == "Retail Banking"
    assert record.confidence == 0.3


def test_new_speciality_confidence_scales_with_the_matched_category():
    # Category is a real, exact match (confidence 1.0); only the speciality
    # is a from-scratch proposal - it should read higher than the floor,
    # since something in this placement is actually verified.
    perfect_category = _base(speciality_action="NEW_SPECIALITY", target_speciality_id="skillsfuture:601",
                              target_speciality_name="Retail Banking")
    weak_category = _base(
        category_match_type="semantic", category_score=None, category_semantic_score=0.65,
        speciality_action="NEW_SPECIALITY", target_speciality_id="skillsfuture:601",
        target_speciality_name="Retail Banking",
    )

    perfect_record = build_classification_outputs([perfect_category], "skillsfuture")[0]
    weak_record = build_classification_outputs([weak_category], "skillsfuture")[0]

    assert perfect_record.confidence == 0.6  # floor 0.3 + 0.3 * 1.0
    assert weak_record.confidence == 0.495  # floor 0.3 + 0.3 * 0.65
    assert perfect_record.confidence > weak_record.confidence > 0.3


def test_new_category_with_no_determinable_service_is_fully_unresolved():
    # No matched baseline categories in this run to vote a service from -
    # category_adaptation leaves target_service_name unset; the contract's
    # all-four-or-null rule means the whole record stays unresolved, not
    # just missing its service.
    placement = _base(
        action="NEW_CATEGORY",
        category_match_type=None,
        category_score=None,
        target_category_id="skillsfuture:501",
        target_category_name="Banking",
        target_service_id=None,
        target_service_name=None,
        speciality_action="NEW_SPECIALITY",
        speciality_match_type=None,
        speciality_score=None,
        speciality_semantic_score=None,
        target_speciality_id="skillsfuture:601",
        target_speciality_name="Retail Banking",
    )
    records = build_classification_outputs([placement], "skillsfuture")

    record = records[0]
    assert (record.service, record.category, record.speciality, record.confidence) == (None, None, None, None)
    assert "No service could be determined" in record.reason


def test_review_ambiguous_category_is_fully_unresolved():
    placement = {
        "tag_id": 9002, "tag_name": "rocket fuel", "source_category_id": 502,
        "source_category_name": "Space Exploration", "action": "REVIEW_AMBIGUOUS",
        "closest_baseline_name": "Banking", "closest_baseline_score": 41.2,
        "target_category_id": None, "target_category_name": None, "needs_review": True,
    }
    records = build_classification_outputs([placement], "skillsfuture")

    record = records[0]
    assert (record.service, record.category, record.speciality, record.confidence) == (None, None, None, None)
    assert "Space Exploration" in record.reason and "Banking" in record.reason


def test_review_ambiguous_reason_surfaces_both_lexical_and_semantic_near_misses():
    # Lexical and semantic near-misses can point at different baseline
    # categories entirely ("Built Environment" is coincidentally close in
    # spelling to "Profit Improvement" but genuinely close in meaning to
    # "Construction & Engineering") - a reviewer needs both, not just one.
    placement = {
        "tag_id": 9004, "tag_name": "aircon maintenance", "source_category_id": 503,
        "source_category_name": "Built Environment", "action": "REVIEW_AMBIGUOUS",
        "closest_baseline_name": "Profit Improvement", "closest_baseline_score": 57.1,
        "closest_semantic_baseline_name": "Construction & Engineering", "closest_semantic_score": 0.5352,
        "target_category_id": None, "target_category_name": None, "needs_review": True,
    }
    records = build_classification_outputs([placement], "skillsfuture")

    reason = records[0].reason
    assert "Construction & Engineering" in reason
    assert "Profit Improvement" in reason


def test_review_new_placement_category_reason_names_the_mismatched_parent():
    # The category matched an existing one by name, but under a different
    # parent (e.g. service) than the source gives it - name both sides of
    # the mismatch, not just a generic "needs manual review".
    placement = {
        "tag_id": 9005, "tag_name": "firewall config", "source_category_id": 504,
        "source_category_name": "Infocomm Tech", "action": "REVIEW_NEW_PLACEMENT",
        "target_category_id": 1, "target_category_name": "Information Technology",
        "unmatched_incoming_parents": ["Totally Different Service"], "existing_parents": ["Industry"],
        "needs_review": True,
    }
    records = build_classification_outputs([placement], "skillsfuture")

    record = records[0]
    assert (record.service, record.category, record.speciality, record.confidence) == (None, None, None, None)
    assert "Information Technology" in record.reason
    assert "Totally Different Service" in record.reason
    assert "Industry" in record.reason



def test_matched_category_with_unresolved_speciality_nulls_the_whole_record():
    # Category (and service) are confidently known, but nothing could be said
    # about a speciality - the contract's all-or-null rule means none of the
    # four fields can be reported alone either.
    placement = _base(speciality_action="REVIEW_AMBIGUOUS")
    records = build_classification_outputs([placement], "skillsfuture")

    record = records[0]
    assert (record.service, record.category, record.speciality, record.confidence) == (None, None, None, None)
    assert "No speciality could be resolved" in record.reason


def test_no_source_category_is_unresolved_and_correctly_unclassified():
    # This specific tag was never categorized in its own source data (that's
    # what NO_SOURCE_CATEGORY means), so it's honestly "unclassified" even
    # though the dataset overall is a Case A source.
    placement = {
        "tag_id": 9003, "tag_name": "unlinked tag", "action": "NO_SOURCE_CATEGORY", "needs_review": True,
    }
    records = build_classification_outputs([placement], "skillsfuture")

    record = records[0]
    assert record.original_classification == "unclassified"
    assert (record.service, record.category, record.speciality, record.confidence) == (None, None, None, None)
    assert "Case B" in record.reason


def _candidate(**overrides):
    placement = {
        "tag_id": 9006, "tag_name": "Fire Prevention", "placement_basis": "review_candidate",
        "source_category_id": 505, "source_category_name": "Air Transport", "action": "REVIEW_AMBIGUOUS",
        "closest_semantic_baseline_name": "Logistics, Shipping & Aviation", "closest_semantic_score": 0.52,
        "target_category_id": None, "target_category_name": None, "needs_review": True,
        "candidate_rank": 2, "candidate_count": 3, "candidate_score": 0.52, "candidate_score_type": "semantic",
        "candidate_category_id": 3, "candidate_category_name": "Logistics, Shipping & Aviation",
        "candidate_service_id": 1, "candidate_service_name": "Industry",
        "speciality_action": "NEW_SPECIALITY", "target_speciality_id": "skillsfuture:tag:9006",
        "target_speciality_name": "Fire Prevention",
    }
    placement.update(overrides)
    return placement


def test_review_candidate_is_a_full_placement_flagged_for_review():
    record = build_classification_outputs([_candidate()], "skillsfuture")[0]

    assert record.needs_review is True
    assert (record.service, record.category, record.speciality) == (
        "Industry", "Logistics, Shipping & Aviation", "Fire Prevention",
    )
    assert record.confidence == 0.52  # the candidate's own similarity
    assert "Review candidate 2 of 3" in record.reason
    assert "Air Transport" in record.reason


def test_review_candidate_confidence_is_its_raw_similarity_and_keeps_the_ranking():
    candidates = [_candidate(candidate_rank=rank, candidate_score=score)
                  for rank, score in ((1, 0.5221), (2, 0.3741), (3, 0.3223))]
    lexical = build_classification_outputs(
        [_candidate(candidate_score=25.0, candidate_score_type="lexical")], "skillsfuture",
    )[0]

    assert [r.confidence for r in build_classification_outputs(candidates, "skillsfuture")] == [0.5221, 0.3741, 0.3223]
    assert lexical.confidence == 0.25


def test_output_validates_against_the_shared_contract():
    unresolved = {
        "tag_id": 9002, "tag_name": "rocket fuel", "action": "REVIEW_AMBIGUOUS",
        "closest_baseline_name": None, "closest_baseline_score": None,
        "target_category_id": None, "target_category_name": None, "needs_review": True,
    }
    records = build_classification_outputs([_content(), unresolved, _candidate()], "skillsfuture")

    # Round-trips through the exact validator Bryan's side uses.
    validated = CLASSIFICATION_OUTPUTS.validate_json(CLASSIFICATION_OUTPUTS.dump_json(records))
    assert len(validated) == 3
    assert [record.needs_review for record in validated] == [False, False, True]


def test_category_scoped_match_becomes_a_resolved_record_in_the_existing_speciality():
    placement = _base(
        placement_basis="category_content",
        speciality_action="SKIP_CATEGORY_SCOPED_MATCH",
        target_speciality_id=3,
        target_speciality_name="Loan Servicing",
        speciality_semantic_score=0.61,
    )

    [record] = build_classification_outputs([placement], source_dataset="skillsfuture")

    assert (record.service, record.category, record.speciality) == ("Industry", "Banking", "Loan Servicing")
    assert record.confidence == 0.61
    assert record.needs_review is False
    assert "closest in meaning to existing speciality 'Loan Servicing' (semantic 0.61)" in record.reason
    CLASSIFICATION_OUTPUTS.validate_python(
        CLASSIFICATION_OUTPUTS.dump_python([record], mode="json")
    )


def test_source_categories_set_aside_by_one_category_per_tag_are_named_in_the_reason():
    placement = _base(
        speciality_action="NEW_SPECIALITY",
        target_speciality_id="skillsfuture:tag:9001",
        target_speciality_name="loan processing",
        other_source_categories=["Insurance", "Wealth"],
    )

    [record] = build_classification_outputs([placement], source_dataset="skillsfuture")

    assert record.speciality == "loan processing"
    assert "Also linked to source categories 'Insurance', 'Wealth'; placed via the one with the most evidence only." \
        in record.reason
