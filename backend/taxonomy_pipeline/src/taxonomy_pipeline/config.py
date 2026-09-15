"""Pipeline configuration: which taxonomy datasets to load and which metrics
to run against them. Loaded from YAML for the CLI, or accepted as the same
shape over JSON by the FastAPI `/runs` route - one schema, two entry points.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class DatasetConfig(BaseModel):
    """One taxonomy dataset to load, addressed by `name` from metric params.

    `type` selects a source implementation registered in `datasets/__init__.py`
    ("sql_dump", "ssic", or "ssoc"). Classification sources require exactly
    one workbook path. `table_map` remaps canonical concepts onto
    whatever the underlying tables are actually called, e.g. the SkillsFuture
    dump's `sf_categories` -> `categories`; concepts left out default to their
    own name.
    """

    name: str
    type: str = "sql_dump"
    paths: list[str]
    table_map: dict[str, str] = Field(default_factory=dict)


class MetricConfig(BaseModel):
    """One metric run, by id (see `metrics/registry.py`), with its params.

    The same metric id may appear more than once with different params -
    e.g. running "integrity" once per dataset.
    """

    id: str
    params: dict[str, Any] = Field(default_factory=dict)


class PipelineConfig(BaseModel):
    """A full pipeline run: datasets to load and metrics to run against them."""

    datasets: list[DatasetConfig]
    metrics: list[MetricConfig]

    def dataset_names(self) -> list[str]:
        return [d.name for d in self.datasets]


def load_config(path: str | Path) -> PipelineConfig:
    """Load a YAML config file. Relative dataset paths are resolved against
    the config file's own directory (not the current working directory), so
    a config stays valid no matter where it's run from.
    """
    path = Path(path)
    raw = yaml.safe_load(path.read_text())
    config = PipelineConfig.model_validate(raw)

    base_dir = path.parent
    for dataset in config.datasets:
        dataset.paths = [
            p if Path(p).is_absolute() else str((base_dir / p).resolve())
            for p in dataset.paths
        ]
    return config
