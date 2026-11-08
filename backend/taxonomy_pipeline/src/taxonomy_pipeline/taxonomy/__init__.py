"""Dataset source registry - the plug-in point for `type:` values in config.

To add a new source type (e.g. a live MariaDB connection reusing
`backend/audit`'s connection env vars), implement `TaxonomySource` in a new
module and add one line to `_BUILDERS` below. Everything else - config
validation, the pipeline, both metrics and the API - is unaffected.
"""
from __future__ import annotations

from ..config import DatasetConfig
from .base import LoadedDataset, TaxonomyDataset, TaxonomySource
from .references.ssic import SsicSource
from .references.ssoc import SsocSource
from .sql_dump import SqlDumpSource
from .synonym_set import SynonymSetSource


def _classification_source(config: DatasetConfig, source_type):
    if len(config.paths) != 1:
        raise ValueError(f"{config.type} requires exactly one workbook path")
    return source_type(config.paths[0])


def _synonym_set_source(config: DatasetConfig) -> SynonymSetSource:
    if len(config.paths) != 1:
        raise ValueError("synonym_set requires exactly one exported synonyms file")
    return SynonymSetSource(config.name, config.paths[0])

_BUILDERS = {
    "sql_dump": lambda cfg: SqlDumpSource(cfg.name, cfg.paths, cfg.table_map),
    "ssic": lambda cfg: _classification_source(cfg, SsicSource),
    "ssoc": lambda cfg: _classification_source(cfg, SsocSource),
    "synonym_set": _synonym_set_source,
}


def build_source(config: DatasetConfig) -> TaxonomySource:
    try:
        builder = _BUILDERS[config.type]
    except KeyError as exc:
        raise ValueError(
            f"Unknown dataset type {config.type!r}; available: {sorted(_BUILDERS)}"
        ) from exc
    return builder(config)


def load_dataset(config: DatasetConfig) -> LoadedDataset:
    return build_source(config).load()


__all__ = ["TaxonomyDataset", "TaxonomySource", "build_source", "load_dataset"]
