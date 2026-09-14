"""The plug-in point for metrics ("tests").

Add a new metric by subclassing `Metric` and registering an instance in
`metrics/registry.py` - it then shows up in `GET /metrics`, is runnable by
id from any config (CLI or API), and needs no other code changed.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..datasets.base import TaxonomyDataset
from ..models import MetricResult


class Metric(ABC):
    id: str
    description: str
    default_params: dict[str, Any] = {}

    def params_schema(self) -> dict[str, Any]:
        """Default parameters, exposed via `GET /metrics` so a caller (e.g. a
        future dashboard) knows what's tunable without reading the source."""
        return dict(self.default_params)

    def resolve_params(self, params: dict[str, Any]) -> dict[str, Any]:
        merged = dict(self.default_params)
        merged.update(params or {})
        return merged

    def require_datasets(
        self, datasets: dict[str, TaxonomyDataset], names: dict[str, str | None]
    ) -> dict[str, TaxonomyDataset] | MetricResult:
        """Resolve each `role -> dataset name` in `names` against loaded
        `datasets`. Returns `{role: TaxonomyDataset}` on success, or an
        error `MetricResult` (ready to return straight from `run`) if any
        name is missing or wasn't loaded for this run.
        """
        missing = [name for name in names.values() if name not in datasets]
        if missing:
            return MetricResult(
                metric_id=self.id,
                status="error",
                summary=f"dataset(s) not loaded for this run: {missing}",
            )
        return {role: datasets[name] for role, name in names.items()}

    @abstractmethod
    def run(
        self, datasets: dict[str, TaxonomyDataset], params: dict[str, Any]
    ) -> MetricResult: ...
