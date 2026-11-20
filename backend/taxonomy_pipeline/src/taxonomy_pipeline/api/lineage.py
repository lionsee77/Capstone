"""`GET /lineage`: the enrichment lineage, read from server-configured directories only.

Which directories it reads is this module's job, the same kind of check as
`proposals.py`'s merge root; building the lineage is the `app.lineage` use case.
No request parameter names a path.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter

from ..app import lineage as use_cases
from ..models import TaxonomyLineage
from .proposals import DEFAULT_MERGE_ROOT

router = APIRouter(tags=["lineage"])

# `integrate` writes integrations/<name> under backend/taxonomy_pipeline when run from there, as the README says.
# Resolved like `DEFAULT_MERGE_ROOT` rather than from the server's working directory.
DEFAULT_INTEGRATIONS_ROOT = Path(__file__).resolve().parents[3] / "integrations"


@router.get("/lineage")
def get_lineage() -> TaxonomyLineage:
    """Every integrated taxonomy version, the chains of rounds through them, and the stored runs that scored them."""
    integrations = Path(os.environ.get("TAXONOMY_INTEGRATIONS_ROOT") or DEFAULT_INTEGRATIONS_ROOT).resolve()
    merges = Path(os.environ.get("TAXONOMY_MERGE_ROOT") or DEFAULT_MERGE_ROOT).resolve()
    return use_cases.taxonomy_lineage(integrations, merges)
