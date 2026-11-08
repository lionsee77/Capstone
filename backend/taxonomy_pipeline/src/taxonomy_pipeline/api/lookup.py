"""`GET /lookup`: check a proposed term against the taxonomy, without needing a run ID.

Which directory it reads is this module's job, the same kind of check as
`lineage.py`; matching is the `app.lookup` use case, the one `taxonomy-pipeline
lookup` calls. No request parameter names a path: `snapshot` is only ever compared
with the names of the rounds under the configured root.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from ..app import lookup as use_cases
from ..matching.lookup import DEFAULT_MIN_SCORE
from ..models import ConceptLevel, TagLookupResult
from .lineage import DEFAULT_INTEGRATIONS_ROOT

router = APIRouter(tags=["lookup"])


@router.get("/lookup")
def lookup(
    q: str,
    level: ConceptLevel | None = None,
    limit: int = use_cases.DEFAULT_LIMIT,
    min_score: float = DEFAULT_MIN_SCORE,
    semantic: bool = False,
    snapshot: str | None = Query(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"),
) -> TagLookupResult:
    """Exact and near matches for `q` in the newest snapshot, or the one named by `snapshot`; approved
    synonyms of `TAXONOMY_SYNONYM_DATASET` (default `original`) count as exact matches."""
    root = Path(os.environ.get("TAXONOMY_INTEGRATIONS_ROOT") or DEFAULT_INTEGRATIONS_ROOT).resolve()
    synonym_dataset = os.environ.get("TAXONOMY_SYNONYM_DATASET") or use_cases.DEFAULT_SYNONYM_DATASET
    try:
        return use_cases.lookup_term(
            q, root, snapshot=snapshot, level=level, limit=limit, min_score=min_score, semantic=semantic,
            synonym_dataset=synonym_dataset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except use_cases.SnapshotNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except use_cases.NoSnapshots as exc:
        raise HTTPException(status_code=409, detail="No integrated taxonomy snapshot is available yet") from exc
    except use_cases.SemanticUnavailable as exc:
        raise HTTPException(status_code=503, detail="Semantic matching is unavailable on this server") from exc
