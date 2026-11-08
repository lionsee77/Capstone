"""A `synonym_set` dataset loads an exported synonyms file, so a scoring config can pin its synonyms."""
from __future__ import annotations

from pathlib import Path

import pytest

from taxonomy_pipeline.config import DatasetConfig
from taxonomy_pipeline.taxonomy import load_dataset
from taxonomy_pipeline.taxonomy.synonym_set import SynonymMapping, SynonymSet


def _write(path: Path) -> Path:
    path.write_text(SynonymSet(name="exported-name", dataset="original", synonyms=[
        SynonymMapping(synonym_id="a", term="JS", normalized_term="js", concept_level="tags", concept_id="1",
                       concept_name="JavaScript"),
        SynonymMapping(synonym_id="b", term="Pitch", normalized_term="pitch", concept_level="tags",
                       concept_id="2", concept_name="Sales Pitch", scope_level="categories", scope_id="7"),
    ]).model_dump_json(), encoding="utf-8")
    return path


def test_loads_an_exported_file_under_the_configs_dataset_name(tmp_path: Path) -> None:
    path = str(_write(tmp_path / "s.json"))
    loaded = load_dataset(DatasetConfig(name="synonyms_v1", type="synonym_set", paths=[path]))
    assert isinstance(loaded, SynonymSet)
    assert (loaded.name, loaded.dataset) == ("synonyms_v1", "original")
    assert loaded.counts() == {"synonyms": 2, "global": 1, "scoped": 1}
    assert [s.scope for s in loaded.synonyms] == [None, ("categories", "7")]


def test_needs_exactly_one_file(tmp_path: Path) -> None:
    path = str(_write(tmp_path / "s.json"))
    with pytest.raises(ValueError, match="exactly one exported synonyms file"):
        load_dataset(DatasetConfig(name="s", type="synonym_set", paths=[path, path]))
