"""`/synonyms`: list, add, remove, resolve, suggest and review synonyms over `app.synonyms`,
under the app's token check.

Adding names the taxonomy by a past run's `run_id` and `dataset`, as `/taxonomy` does,
since the API doesn't read config files from callers. A file a suggestion source reads
must sit under the allowed data roots, the same request-level check `POST /runs` makes.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, ValidationError

from ..app import synonyms as use_cases
from ..audit.models import Text
from ..synonyms import (
    ConflictingSynonym,
    DuplicateSynonym,
    Resolution,
    ScopeLevel,
    SuggestionReport,
    Synonym,
    SynonymCreate,
    SynonymError,
    SynonymLevel,
    SynonymList,
    SynonymNotFound,
    SynonymQuery,
    UnknownConcept,
)
from ..synonyms.models import SynonymStatus
from .security import data_roots

router = APIRouter()


class SynonymAddRequest(SynonymCreate):
    """A synonym to add, plus the run whose dataset the concept is checked against."""

    run_id: Text


class SynonymRemoveRequest(BaseModel):
    """Why a synonym is being removed, and by whom."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: Text
    actor: Text = "unspecified"


class SynonymSuggestRequest(BaseModel):
    """Run one suggestion source over a past run's dataset."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: Text
    dataset: Text
    source: Text
    path: Text | None = None
    actor: Text = "unspecified"


class SynonymReviewRequest(BaseModel):
    """Why a proposed synonym is approved or rejected; approving checks it against a past run's dataset."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: Text
    actor: Text = "unspecified"
    run_id: Text | None = None


@router.get("/synonyms")
def get_synonyms(
    dataset: str | None = None, concept_level: SynonymLevel | None = None, concept_id: str | None = None,
    term: str | None = None, include_removed: bool = False, status: SynonymStatus | None = None,
    offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
) -> SynonymList:
    """Approved synonyms (with removed ones on `include_removed`, or exactly one `status`, e.g. `proposed`
    for the review queue), filtered and paginated; read-only."""
    try:
        query = SynonymQuery(
            dataset=dataset, concept_level=concept_level, concept_id=concept_id, term=term,
            include_removed=include_removed, status=status, offset=offset, limit=limit,
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


@router.post("/synonyms/suggest", status_code=201)
def suggest_synonyms(request: SynonymSuggestRequest) -> SuggestionReport:
    """Store one source's suggestions as `proposed` synonyms: 403 for a file outside the data roots,
    404 for an unknown run or dataset, 422 for an unknown source or a missing/unexpected file."""
    if request.path is not None and not any(
        Path(request.path).resolve().is_relative_to(root) for root in data_roots()
    ):
        raise HTTPException(status_code=403, detail="path is outside the allowed data directories")
    try:
        return use_cases.suggest_synonyms(
            request.source, dataset=request.dataset, run_id=request.run_id, path=request.path, actor=request.actor,
        )
    except UnknownConcept as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/synonyms/{synonym_id}/approve")
def approve_synonym(synonym_id: str, request: SynonymReviewRequest) -> Synonym:
    """Approve a proposed synonym after the same checks as adding one: 404 for no such proposal or an unknown
    run/concept, 409 when the term already means another concept, 422 without `run_id`."""
    if request.run_id is None:
        raise HTTPException(status_code=422, detail="approving needs run_id: the taxonomy it is checked against")
    try:
        return use_cases.review_synonym(
            synonym_id, approve=True, reason=request.reason, actor=request.actor, run_id=request.run_id,
        )
    except (SynonymNotFound, UnknownConcept) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (DuplicateSynonym, ConflictingSynonym) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/synonyms/{synonym_id}/reject")
def reject_synonym(synonym_id: str, request: SynonymReviewRequest) -> Synonym:
    """Reject a proposed synonym; it is kept so the same suggestion isn't offered again. 404 for no such proposal."""
    try:
        return use_cases.review_synonym(synonym_id, approve=False, reason=request.reason, actor=request.actor)
    except SynonymNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/synonyms/{synonym_id}/remove")
def remove_synonym(synonym_id: str, request: SynonymRemoveRequest) -> Synonym:
    """Mark an approved synonym removed (its row and history are kept): 404 if there is none."""
    try:
        return use_cases.remove_synonym(synonym_id, reason=request.reason, actor=request.actor)
    except SynonymNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
