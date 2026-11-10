"""`/rounds`: run an enrichment round from the dashboard, a thin layer over `app.rounds`.

Which directories a round may use is this module's job, the same kind of check as
`lineage.py`. No request names a path: a round ID and an uploaded file's name are each
validated by the use case against what's on disk. Stages run as background tasks like
`POST /runs`, so poll `GET /rounds/{round_id}` for status.

Upload is a raw `PUT` body rather than a multipart form, so it adds no dependency and the
dashboard's proxy can forward the bytes unchanged. `Content-Length` is checked before reading
it, so an oversized upload is refused without being held in memory.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict

from ..app import rounds as use_cases
from ..enrichment.models import ReviewDecision, ReviewQueue, SourceTagRef
from ..models import RoundFile, RoundOptions, RoundSettings, RoundState
from ..taxonomy.flat_import import SourceMapping
from .lineage import DEFAULT_INTEGRATIONS_ROOT
from .proposals import DEFAULT_MERGE_ROOT

router = APIRouter(prefix="/rounds", tags=["rounds"])

DEFAULT_ROUNDS_ROOT = Path(__file__).resolve().parents[3] / "rounds"


def _roots() -> use_cases.RoundRoots:
    return use_cases.RoundRoots(
        rounds=Path(os.environ.get("TAXONOMY_ROUNDS_ROOT") or DEFAULT_ROUNDS_ROOT).resolve(),
        integrations=Path(os.environ.get("TAXONOMY_INTEGRATIONS_ROOT") or DEFAULT_INTEGRATIONS_ROOT).resolve(),
        merge_runs=Path(os.environ.get("TAXONOMY_MERGE_ROOT") or DEFAULT_MERGE_ROOT).resolve(),
        template=use_cases.DEFAULT_TEMPLATE,
    )


class NewRound(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round_id: str
    baseline_snapshot: str


class RoundConfiguration(BaseModel):
    """The round form: how to read the source, and the switches for this round."""

    model_config = ConfigDict(extra="forbid")

    mapping: SourceMapping
    settings: RoundSettings = RoundSettings()


class DecisionSubmission(BaseModel):
    """Decisions to save, and earlier ones to take back. `reviewer` is self-reported, as the CLI's `--actor` is."""

    model_config = ConfigDict(extra="forbid")

    reviewer: str
    decisions: list[ReviewDecision] = []
    clear_tags: list[SourceTagRef] = []
    clear_proposals: list[str] = []


class BulkDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer: str
    create_below: float | None = None
    reuse_above: float | None = None


class BulkDraftResult(BaseModel):
    round: RoundState
    counts: dict[str, int]


class RoundConfigText(BaseModel):
    config: str


def _translate(exc: Exception) -> HTTPException:
    """One mapping from use-case errors to statuses, for every route here."""
    if isinstance(exc, use_cases.RoundNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, use_cases.RoundConflict):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


_HANDLED = (use_cases.RoundNotFound, use_cases.RoundConflict, ValueError)


@router.get("/options")
def get_options() -> RoundOptions:
    """What the new-round form offers: snapshots, services, and whether LLM steps are allowed."""
    return use_cases.round_options(_roots())


@router.get("")
def list_rounds() -> list[RoundState]:
    return use_cases.list_rounds(_roots())


@router.post("", status_code=201)
def create_round(body: NewRound) -> RoundState:
    try:
        return use_cases.create_round(_roots(), body.round_id, body.baseline_snapshot)
    except _HANDLED as exc:
        raise _translate(exc) from exc


@router.get("/{round_id}")
def get_round(round_id: str) -> RoundState:
    try:
        return use_cases.get_round(_roots(), round_id)
    except _HANDLED as exc:
        raise _translate(exc) from exc


@router.delete("/{round_id}", status_code=204)
def delete_round(round_id: str) -> None:
    try:
        use_cases.delete_round(_roots(), round_id)
    except _HANDLED as exc:
        raise _translate(exc) from exc


@router.get("/{round_id}/config")
def get_config(round_id: str) -> RoundConfigText:
    try:
        return RoundConfigText(config=use_cases.round_config_text(_roots(), round_id))
    except _HANDLED as exc:
        raise _translate(exc) from exc


@router.put("/{round_id}/config")
def configure_round(round_id: str, body: RoundConfiguration) -> RoundState:
    try:
        return use_cases.configure_round(_roots(), round_id, body.mapping, body.settings)
    except _HANDLED as exc:
        raise _translate(exc) from exc


@router.get("/{round_id}/files")
def list_files(round_id: str) -> list[RoundFile]:
    try:
        return use_cases.list_files(_roots(), round_id)
    except _HANDLED as exc:
        raise _translate(exc) from exc


@router.get("/{round_id}/files/{filename}")
def describe_file(round_id: str, filename: str, sheet: str | None = None) -> RoundFile:
    try:
        return use_cases.describe_file(_roots(), round_id, filename, sheet)
    except _HANDLED as exc:
        raise _translate(exc) from exc


@router.put("/{round_id}/files/{filename}", status_code=201)
async def upload_file(round_id: str, filename: str, request: Request) -> RoundFile:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > use_cases.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="That file is larger than the upload limit")
    content = bytearray()
    async for chunk in request.stream():  # a missing or false Content-Length mustn't get past the limit either
        content.extend(chunk)
        if len(content) > use_cases.MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="That file is larger than the upload limit")
    try:
        # On a worker thread: saving and reading a large table on the event loop would stall every other request.
        return await run_in_threadpool(use_cases.add_file, _roots(), round_id, filename, bytes(content))
    except _HANDLED as exc:
        raise _translate(exc) from exc


@router.delete("/{round_id}/files/{filename}", status_code=204)
def remove_file(round_id: str, filename: str) -> None:
    try:
        use_cases.remove_file(_roots(), round_id, filename)
    except _HANDLED as exc:
        raise _translate(exc) from exc


@router.post("/{round_id}/run", status_code=202)
def run_round(round_id: str, background_tasks: BackgroundTasks) -> RoundState:
    """Run or resume the stages before review. It spends LLM credits only if the round and the server allow it."""
    roots = _roots()
    try:
        state, stages = use_cases.start_round(roots, round_id)
    except _HANDLED as exc:
        raise _translate(exc) from exc
    background_tasks.add_task(use_cases.run_stages, roots, round_id, stages)
    return state


@router.get("/{round_id}/review")
def get_review(
    round_id: str, state: str = "undecided", q: str | None = None, offset: int = 0, limit: int = 50,
) -> ReviewQueue:
    try:
        return use_cases.get_review(_roots(), round_id, state=state, query=q, offset=offset, limit=limit)
    except _HANDLED as exc:
        raise _translate(exc) from exc


@router.post("/{round_id}/decisions")
def save_decisions(round_id: str, body: DecisionSubmission) -> RoundState:
    try:
        return use_cases.save_decisions(
            _roots(), round_id, body.reviewer, body.decisions, clear_tags=body.clear_tags,
            clear_proposals=body.clear_proposals,
        )
    except _HANDLED as exc:
        raise _translate(exc) from exc


@router.post("/{round_id}/bulk-decide")
def draft_decisions(round_id: str, body: BulkDraftRequest) -> BulkDraftResult:
    try:
        state, counts = use_cases.draft_decisions(
            _roots(), round_id, body.reviewer, create_below=body.create_below, reuse_above=body.reuse_above,
        )
    except _HANDLED as exc:
        raise _translate(exc) from exc
    return BulkDraftResult(round=state, counts=counts)


@router.post("/{round_id}/apply", status_code=202)
def apply_decisions(round_id: str, background_tasks: BackgroundTasks) -> RoundState:
    """Rebuild the merge preview with the saved decisions, in the background."""
    roots = _roots()
    try:
        state, stages = use_cases.start_apply(roots, round_id)
    except _HANDLED as exc:
        raise _translate(exc) from exc
    background_tasks.add_task(use_cases.run_stages, roots, round_id, stages)
    return state


@router.post("/{round_id}/integrate", status_code=202)
def integrate_round(round_id: str, background_tasks: BackgroundTasks) -> RoundState:
    """Generate the enriched snapshot and score it, in the background, once nothing is held for review."""
    roots = _roots()
    try:
        state, stages = use_cases.start_integrate(roots, round_id)
    except _HANDLED as exc:
        raise _translate(exc) from exc
    background_tasks.add_task(use_cases.run_stages, roots, round_id, stages)
    return state
