"""The plug-in point for metrics ("tests").

Add a new metric by subclassing `Metric` and registering an instance in
`metrics/registry.py` - it then shows up in `GET /metrics`, is runnable by
id from any config (CLI or API), and needs no other code changed.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..datasets.base import LoadedDataset, TaxonomyDataset
from ..models import MetricResult


class Metric(ABC):
    id: str
    description: str
    default_params: dict[str, Any] = {}
    # The param naming the taxonomy this metric scores (e.g. integrity's
    # "dataset", breadth's "target"), or None for a metric that doesn't score
    # one taxonomy. `integrate` uses it to point a round's config at the
    # enriched snapshot, so a new scoring metric only needs to set it. It also
    # puts the metric under the metric definitions version, and tells
    # `definitions.check_comparable` which param may differ between a baseline
    # run and an enriched one (see `definitions.py`).
    subject_param: str | None = None

    def params_schema(self) -> dict[str, Any]:
        """Default parameters, exposed via `GET /metrics` so a caller (e.g. a
        future dashboard) knows what's tunable without reading the source."""
        return dict(self.default_params)

    def definition(self) -> dict[str, Any]:
        """What this metric's scores depend on besides a run's params: its
        defaults, plus any fixed part of its formula that no param exposes
        (a pinned model revision, a versioned text profile). Override to add
        those. For a metric with a `subject_param`, this is part of the metric
        definitions version (`definitions.py`), so changing it means a bump."""
        return {"default_params": dict(self.default_params)}

    def resolve_params(self, params: dict[str, Any]) -> dict[str, Any]:
        merged = dict(self.default_params)
        merged.update(params or {})
        return merged

    def require_datasets(
        self, datasets: dict[str, LoadedDataset], names: dict[str, str | None],
        expected_types: dict[str, type] | None = None,
    ) -> dict[str, LoadedDataset] | MetricResult:
        """Resolve each `role -> dataset name` in `names` against loaded
        `datasets`. Returns resolved datasets or an error result for missing
        names or wrong types. Roles require TaxonomyDataset by default;
        expected_types can specify classification reference types per role.
        """
        missing = [name for name in names.values() if name not in datasets]
        if missing:
            return MetricResult(
                metric_id=self.id,
                status="error",
                summary=f"dataset(s) not loaded for this run: {missing}",
            )
        resolved = {role: datasets[name] for role, name in names.items()}
        for role, dataset in resolved.items():
            expected = (expected_types or {}).get(role, TaxonomyDataset)
            if not isinstance(dataset, expected):
                return MetricResult(
                    metric_id=self.id, status="error",
                    summary=f"{role} must be {expected.__name__}; got {type(dataset).__name__}",
                )
        return resolved

    @abstractmethod
    def run(
        self, datasets: dict[str, LoadedDataset], params: dict[str, Any]
    ) -> MetricResult: ...
