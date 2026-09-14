"""Dataset source registry - the plug-in point for `type:` values in config.

To add a new source type (e.g. a live MariaDB connection reusing
`backend/audit`'s connection env vars), implement `TaxonomySource` in a new
module and add one line to `_BUILDERS` below. Everything else - config
validation, the pipeline, both metrics and the API - is unaffected.
"""
from __future__ import annotations

from ..config import DatasetConfig
from .base import TaxonomyDataset, TaxonomySource
from .sql_dump import SqlDumpSource

_BUILDERS = {
    "sql_dump": lambda cfg: SqlDumpSource(cfg.name, cfg.paths, cfg.table_map),
}


def build_source(config: DatasetConfig) -> TaxonomySource:
    try:
        builder = _BUILDERS[config.type]
    except KeyError as exc:
        raise ValueError(
            f"Unknown dataset type {config.type!r}; available: {sorted(_BUILDERS)}"
        ) from exc
    return builder(config)


def load_dataset(config: DatasetConfig) -> TaxonomyDataset:
    return build_source(config).load()


__all__ = ["TaxonomyDataset", "TaxonomySource", "build_source", "load_dataset"]
