"""Share placement identity between resolution and its review projections."""
from __future__ import annotations

from ..matching.names import normalize
from .models import ClassificationOutput, PlacementPath, PreviewRow

PathKey = tuple[str, str, str]


# Match reviewed paths using intake's parent-scoped normalization.
def placement_path(item: PlacementPath | ClassificationOutput | PreviewRow) -> PathKey:
    return item.service, normalize(item.category), normalize(item.speciality)
