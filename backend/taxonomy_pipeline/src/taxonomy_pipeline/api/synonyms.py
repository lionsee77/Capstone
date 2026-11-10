"""`/synonyms`: list, add and remove synonyms over `app.synonyms`, under the app's token check.

Adding names the taxonomy by a past run's `run_id` and `dataset`, as `/taxonomy` does,
since the API doesn't read config files from callers. Every route maps the use case's
errors the same way (`_errors`): 404 for an unknown run, dataset, concept or synonym
(or a dataset whose files can't be loaded), 409 for a duplicate or a term that already
means another concept where it would apply, 422 for any other refused request (such as a
scope the concept isn't in), and 503 when the synonym store itself can't be used.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, ValidationError

from ..app import synonyms as use_cases
from ..audit.models import Text
from ..synonyms import (
    ConflictingSynonym,
    DuplicateSynonym,
    Resolution,
    Synonym,
    SynonymCreate,
    SynonymLevel,
    SynonymList,
    SynonymNotFound,
    SynonymProblem,
    SynonymQuery,
    TaxonomyUnavailable,
    UnknownConcept,
    UnsupportedSchema,
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


@contextmanager
def _errors() -> Iterator[None]:
    """Translate the use case's errors into HTTP statuses, so none of them surfaces as a 500."""
    try:
        yield
    except (UnknownConcept, TaxonomyUnavailable, SynonymNotFound) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (DuplicateSynonym, ConflictingSynonym) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (UnsupportedSchema, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail=f"the synonym store is unavailable: {exc}") from exc
    except ValueError as exc:  # SynonymError, validation, or the audit store refusing its schema
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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
    with _errors():
        return use_cases.list_synonyms(query)


@router.get("/synonyms/problems")
def get_synonym_problems(run_id: str, dataset: str) -> list[SynonymProblem]:
    """Approved synonyms of a past run's dataset that don't resolve as recorded (stale, shadowed or ambiguous)."""
    with _errors():
        return use_cases.synonym_problems(dataset, run_id=run_id)


@router.get("/synonyms/resolve")
def resolve_synonym(
    run_id: str, dataset: str, term: str, level: SynonymLevel | None = None,
    within_level: SynonymLevel | None = None, within_id: str | None = None,
) -> Resolution:
    """What `term` means in a past run's dataset: `resolved`, `ambiguous` or `unknown`, with each match
    flagged `via` name or synonym. `within_level`/`within_id` name the branch the lookup is made in;
    the use case checks them (both or neither; an unknown branch is 404)."""
    with _errors():
        return use_cases.resolve_term(term, dataset=dataset, run_id=run_id, level=level,
                                      within_level=within_level, within_id=within_id)


@router.post("/synonyms", status_code=201)
def post_synonym(request: SynonymAddRequest) -> Synonym:
    """Approve a synonym for one existing concept (see the module docstring for statuses)."""
    create = SynonymCreate.model_validate(request.model_dump(exclude={"run_id"}))
    with _errors():
        return use_cases.add_synonym(create, run_id=request.run_id)


@router.post("/synonyms/{synonym_id}/remove")
def remove_synonym(synonym_id: str, request: SynonymRemoveRequest) -> Synonym:
    """Mark an approved synonym removed (its row and history are kept): 404 if there is none."""
    with _errors():
        return use_cases.remove_synonym(synonym_id, reason=request.reason, actor=request.actor)
