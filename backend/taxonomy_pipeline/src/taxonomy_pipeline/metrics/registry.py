"""Central list of available metrics. Import and add your `Metric` subclass
here to make it runnable from both the CLI and the API - this is the only
file a new metric needs to touch outside its own module.
"""
from __future__ import annotations

from .base import Metric
from .breadth import BreadthMetric
from .depth import DepthMetric
from .integrity import IntegrityMetric
from .saturation import SaturationMetric

METRICS: dict[str, Metric] = {
    m.id: m for m in (IntegrityMetric(), DepthMetric(), BreadthMetric(), SaturationMetric())
}


def get_metric(metric_id: str) -> Metric:
    try:
        return METRICS[metric_id]
    except KeyError as exc:
        raise ValueError(f"Unknown metric {metric_id!r}; available: {sorted(METRICS)}") from exc
