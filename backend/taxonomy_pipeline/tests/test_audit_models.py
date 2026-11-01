"""Invalid or ambiguous audit data is rejected before any history is written."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from taxonomy_pipeline.audit import ChangeEvent, ChangeQuery, LinkedReview


@pytest.mark.parametrize("fields", [
    {"reason": " "}, {"actor": " "}, {"concept_ref": ""}, {"action": "edit"},
    {"occurred_at": "2026-10-30T00:00:00"}, {"scope": "review"},
    {"action": "review_decision", "scope": "review", "decision": None},
])
# Required identity, reason, action and timezone must remain meaningful in every event.
def test_rejects_incomplete_or_misleading_events(audit_event: ChangeEvent, fields: dict) -> None:
    with pytest.raises(ValidationError):
        ChangeEvent.model_validate(audit_event.model_dump() | fields)


@pytest.mark.parametrize("fields", [
    {"limit": 201}, {"offset": -1}, {"since": "2026-10-30"},
    {"since": "2026-10-31T00:00:00Z", "until": "2026-10-30T00:00:00Z"},
])
# Invalid filters fail consistently for CLI and API callers.
def test_rejects_invalid_history_filters(fields: dict) -> None:
    with pytest.raises(ValidationError):
        ChangeQuery(**fields)


# Validated models reject field reassignment before unvalidated values can reach internal adapters.
def test_validated_audit_models_reject_field_reassignment(audit_event: ChangeEvent) -> None:
    review = LinkedReview(decision_id="d1", decision={"action": "create"})
    for model, field, value in ((audit_event, "actor", " "), (ChangeQuery(), "limit", -1),
                                (review, "actor", " ")):
        with pytest.raises(ValidationError, match="frozen"):
            setattr(model, field, value)
