"""Expose history queries under the app's existing authentication, with no write routes."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import ValidationError

from ..app.changes import changes
from ..audit.models import ChangeAction, ChangeHistory, ChangeLevel, ChangeQuery

router = APIRouter()


@router.get("/changes")
# Translate HTTP query parameters into the same filters used by the CLI.
def get_changes(
    dataset: str | None = None, snapshot_id: str | None = None, concept_level: ChangeLevel | None = None,
    concept_id: str | None = None, concept_ref: str | None = None, action: ChangeAction | None = None,
    proposal_id: str | None = None, proposal_namespace: str | None = None, decision_id: str | None = None,
    since: datetime | None = None, until: datetime | None = None,
    offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
) -> ChangeHistory:
    """Read audit history with ANDed filters and inclusive timezone-aware date bounds.

    Scope concept IDs by dataset, snapshot_id and concept_level for a unique
    concept lookup. Invalid filters return 422; this endpoint never mutates history.
    """
    try:
        filters = ChangeQuery(
            dataset=dataset, snapshot_id=snapshot_id, concept_level=concept_level, concept_id=concept_id,
            concept_ref=concept_ref, action=action, proposal_id=proposal_id, proposal_namespace=proposal_namespace,
            decision_id=decision_id, since=since, until=until, offset=offset, limit=limit,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return changes(filters)
