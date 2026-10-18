"""Expose saved merge runs within a configured directory, without accepting arbitrary server paths."""
from __future__ import annotations

import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import ValidationError

from ..classification.models import ProposalReviewReport
from ..classification.review_view import ProposalNotFound, load_review_view
from ..proposals.models import Level

router = APIRouter(prefix="/proposals", tags=["proposals"])


# Resolve one merge directory and its report strictly inside the server-configured root, including symlinks.
def _merge_directory(merge_run: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", merge_run):
        raise HTTPException(status_code=422, detail="merge_run must be a directory name, not a path")
    root = Path(os.environ.get("TAXONOMY_MERGE_ROOT", "merge_runs")).resolve()
    directory = (root / merge_run).resolve()
    report = (directory / "resolution.json").resolve()
    if not directory.is_relative_to(root) or not report.is_relative_to(root):
        raise HTTPException(status_code=404, detail="Merge run not found")
    return directory


# Map saved-report failures consistently for both proposal endpoints without exposing raw input data.
def _read(
    merge_run: str, *, proposal_id: str | None = None,
    level: Level | None = None, offset: int = 0, limit: int = 50,
) -> ProposalReviewReport:
    directory = _merge_directory(merge_run)
    try:
        return load_review_view(directory, proposal_id=proposal_id, level=level, offset=offset, limit=limit)
    except (FileNotFoundError, ProposalNotFound) as exc:
        raise HTTPException(status_code=404, detail="Merge run or proposal not found") from exc
    except ValidationError as exc:
        raise HTTPException(status_code=409, detail="Invalid saved resolution report; rerun merge-preview") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=409, detail="Saved resolution report is unreadable") from exc


@router.get("")
# List a bounded page of proposals while retaining every candidate match for each returned proposal.
def list_proposals(
    merge_run: str, level: Level | None = None,
    offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=200),
) -> ProposalReviewReport:
    return _read(merge_run, level=level, offset=offset, limit=limit)


@router.get("/{proposal_id}")
# Return one proposal in the same envelope as list and CLI responses.
def get_proposal(proposal_id: str, merge_run: str) -> ProposalReviewReport:
    return _read(merge_run, proposal_id=proposal_id, limit=1)
