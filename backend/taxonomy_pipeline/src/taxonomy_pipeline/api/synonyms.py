"""`/synonyms`: list, add and remove synonyms over `app.synonyms`, under the app's token check.

Adding names the taxonomy by a past run's `run_id` and `dataset`, as `/taxonomy` does,
since the API doesn't read config files from callers.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, ValidationError

from ..app import synonyms as use_cases
from ..audit.models import Text
from ..synonyms import (
    ConflictingSynonym,
    DuplicateSynonym,
    Resolution,
    ScopeLevel,
    Synonym,
    SynonymCreate,
    SynonymError,
    SynonymLevel,
    SynonymList,
    SynonymNotFound,
    SynonymQuery,
    UnknownConcept,
)

router = APIRouter()


class SynonymAddRequest(SynonymCreate):
    """A synonym to add, plus the run whose dataset the concept is checked against."""

    run_id: Text


class SynonymRemoveRequest(BaseModel):
    """Why a synonym is being removed, and by whom."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: Text
    actor: Text = "unspecified"


@router.get("/synonyms")
def get_synonyms(
    dataset: str | None = None, concept_level: SynonymLevel | None = None, concept_id: str | None = None,
    term: str | None = None, include_removed: bool = False,
    offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
) -> SynonymList:
    """Approved synonyms (and removed ones with `include_removed`), filtered and paginated; read-only."""
    try:
        query = SynonymQuery(
            dataset=dataset, concept_level=concept_level, concept_id=concept_id, term=term,
            include_removed=include_removed, offset=offset, limit=limit,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return use_cases.list_synonyms(query)


@router.get("/synonyms/resolve")
def resolve_synonym(
    run_id: str, dataset: str, term: str, level: SynonymLevel | None = None,
    within_level: ScopeLevel | None = None, within_id: str | None = None,
) -> Resolution:
    """What `term` means in a past run's dataset: `resolved`, `ambiguous` or `unknown`, with each match
    flagged `via` name or synonym. `within_level`/`within_id` name the branch the lookup is made in."""
    if (within_level is None) != (within_id is None):
        raise HTTPException(status_code=422, detail="give both within_level and within_id, or neither")
    within = (within_level, within_id) if within_level and within_id else None
    try:
        return use_cases.resolve_term(term, dataset=dataset, run_id=run_id, level=level, within=within)
    except UnknownConcept as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/synonyms", status_code=201)
def post_synonym(request: SynonymAddRequest) -> Synonym:
    """Approve a synonym for one existing concept: 404 for an unknown run, dataset or concept,
    409 if it is already a synonym of that concept or the term already means another concept
    where it would apply, 422 for a refused term or a scope the concept isn't in."""
    create = SynonymCreate.model_validate(request.model_dump(exclude={"run_id"}))
    try:
        return use_cases.add_synonym(create, run_id=request.run_id)
    except UnknownConcept as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (DuplicateSynonym, ConflictingSynonym) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SynonymError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/synonyms/{synonym_id}/remove")
def remove_synonym(synonym_id: str, request: SynonymRemoveRequest) -> Synonym:
    """Mark an approved synonym removed (its row and history are kept): 404 if there is none."""
    try:
        return use_cases.remove_synonym(synonym_id, reason=request.reason, actor=request.actor)
    except SynonymNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
