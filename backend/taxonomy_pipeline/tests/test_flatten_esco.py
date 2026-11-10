"""ESCO's flatten script joins its linked CSVs into one table that `import-source` turns into a dump (#101)."""
from __future__ import annotations

import csv
import importlib.util
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest

from taxonomy_pipeline.app.sources import import_source
from taxonomy_pipeline.config import load_config
from taxonomy_pipeline.taxonomy import load_dataset

ESCO_DIR = Path(__file__).resolve().parents[2] / "esco"
CONFIGS = Path(__file__).resolve().parents[1] / "configs"
OCC = "http://data.europa.eu/esco/occupation/"
SKILL = "http://data.europa.eu/esco/skill/"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("flatten_esco", ESCO_DIR / "flatten_esco.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


flatten_esco = _load_script()


def _write(path: Path, headers: list[str], rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerows([headers, *rows])


@pytest.fixture
def raw(tmp_path: Path) -> Path:
    """A miniature ESCO export: two occupations, three skills, essential and optional links."""
    folder = tmp_path / "raw"
    folder.mkdir()
    _write(folder / "occupations_en.csv", ["conceptUri", "preferredLabel", "iscoGroup", "description"], [
        [f"{OCC}a", "data scientist", "2511", "long text the join never reads"],
        [f"{OCC}b", "baker", "7512", ""],
    ])
    _write(folder / "skills_en.csv", ["conceptUri", "preferredLabel", "description"], [
        [f"{SKILL}1", "Python (computer programming)", "A programming language."],
        [f"{SKILL}2", "statistics", "The study of data."],
        [f"{SKILL}3", "bake bread", ""],
    ])
    _write(folder / "occupationSkillRelations_en.csv", ["occupationUri", "relationType", "skillUri"], [
        [f"{OCC}a", "essential", f"{SKILL}1"],
        [f"{OCC}a", "optional", f"{SKILL}2"],
        [f"{OCC}b", "essential", f"{SKILL}3"],
    ])
    return folder


def test_joins_occupations_and_skills_into_one_row_per_link_keeping_its_relation_type(raw: Path) -> None:
    result = flatten_esco.flatten(raw)

    assert result.rows == [
        {"isco_group": "2511", "occupation": "data scientist", "skill": "Python (computer programming)",
         "skill_description": "A programming language.", "relation_type": "essential"},
        {"isco_group": "2511", "occupation": "data scientist", "skill": "statistics",
         "skill_description": "The study of data.", "relation_type": "optional"},
        {"isco_group": "7512", "occupation": "baker", "skill": "bake bread", "skill_description": "",
         "relation_type": "essential"},
    ]
    assert result.links_read == 3


def test_fills_the_relation_type_of_a_pre_filtered_export_only_when_told_to(raw: Path) -> None:
    _write(raw / "occupationSkillRelations_en.csv", ["occupationUri", "skillUri"], [
        [f"{OCC}a", f"{SKILL}1"], [f"{OCC}a", f"{SKILL}2"],
    ])

    blank = flatten_esco.flatten(raw)
    assumed = flatten_esco.flatten(raw, assume_relation_type="essential")

    assert {row["relation_type"] for row in blank.rows} == {""} and not blank.relation_type_column
    assert "keeps no rows" in blank.notes[0]
    assert {row["relation_type"] for row in assumed.rows} == {"essential"}


def test_refuses_to_assume_a_relation_type_the_export_already_has(raw: Path) -> None:
    with pytest.raises(ValueError, match="has a relationType column"):
        flatten_esco.flatten(raw, assume_relation_type="essential")


def test_refuses_a_repeated_link_whose_relation_type_differs(raw: Path) -> None:
    with (raw / "occupationSkillRelations_en.csv").open("a", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerow([f"{OCC}a", "optional", f"{SKILL}1"])

    with pytest.raises(ValueError, match=r"Relations_en\.csv:5: link .* repeats line 2 as 'optional'"):
        flatten_esco.flatten(raw)


def test_drops_an_exactly_repeated_occupation_or_skill_row(raw: Path) -> None:
    with (raw / "skills_en.csv").open("a", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerow([f"{SKILL}3", "bake bread", ""])
    with (raw / "occupations_en.csv").open("a", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerow([f"{OCC}b", "baker", "7512", ""])

    result = flatten_esco.flatten(raw)

    assert result.skills.repeats == 1 and result.occupations.repeats == 1
    assert result.skills.repeats_differing_elsewhere == 0
    assert len(result.rows) == 3


def test_drops_a_repeat_that_differs_only_in_a_column_the_join_ignores_and_counts_it(raw: Path) -> None:
    with (raw / "occupations_en.csv").open("a", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerow([f"{OCC}b", "baker", "7512", "a newer description"])

    result = flatten_esco.flatten(raw)

    assert result.occupations.repeats == 1 and result.occupations.repeats_differing_elsewhere == 1


def test_refuses_a_repeated_uri_whose_data_differs(raw: Path) -> None:
    with (raw / "skills_en.csv").open("a", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerow([f"{SKILL}3", "bake pastry", ""])

    with pytest.raises(ValueError, match=r"skills_en\.csv:5: conceptUri .*3 repeats line 4 with different data"):
        flatten_esco.flatten(raw)


def test_names_file_lines_not_record_numbers_after_a_multi_line_field(raw: Path) -> None:
    _write(raw / "skills_en.csv", ["conceptUri", "preferredLabel", "altLabels"], [
        [f"{SKILL}1", "Python (computer programming)", "python\nPython 3\npy"],  # lines 2-4
        [f"{SKILL}2", "statistics", ""],                                          # line 5
        [f"{SKILL}1", "Python", ""],                                              # line 6
    ])

    with pytest.raises(ValueError, match=r"skills_en\.csv:6: conceptUri .*1 repeats line 2 with different data"):
        flatten_esco.flatten(raw)


def test_skips_links_to_unknown_concepts_and_repeated_links(raw: Path) -> None:
    with (raw / "occupationSkillRelations_en.csv").open("a", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow([f"{OCC}a", "essential", f"{SKILL}1"])        # repeat
        writer.writerow([f"{OCC}gone", "essential", f"{SKILL}1"])     # unknown occupation
        writer.writerow([f"{OCC}b", "essential", f"{SKILL}gone"])     # unknown skill

    result = flatten_esco.flatten(raw)

    assert result.repeated_links == 1 and result.dangling_links == 2
    assert len(result.rows) == 3


def test_refuses_an_occupation_without_a_four_digit_isco_unit_group(raw: Path) -> None:
    _write(raw / "occupations_en.csv", ["conceptUri", "preferredLabel", "iscoGroup"], [[f"{OCC}a", "x", "25"]])

    with pytest.raises(ValueError, match="malformed iscoGroup '25'"):
        flatten_esco.flatten(raw)


def test_names_the_missing_columns_of_a_file_from_another_export(raw: Path) -> None:
    _write(raw / "skills_en.csv", ["uri", "label"], [[f"{SKILL}1", "x"]])

    with pytest.raises(ValueError, match=r"missing column\(s\) \['conceptUri', 'preferredLabel'\]"):
        flatten_esco.flatten(raw)


def test_command_writes_the_flat_csv_and_fails_cleanly_on_a_bad_export(raw: Path, capsys) -> None:
    assert flatten_esco.main(["--raw", str(raw)]) == 0
    with (raw / "esco_flat.csv").open(encoding="utf-8") as stream:
        assert list(csv.DictReader(stream))[0]["occupation"] == "data scientist"
    assert "wrote 3 occupation-skill rows" in capsys.readouterr().out

    (raw / "skills_en.csv").unlink()
    assert flatten_esco.main(["--raw", str(raw)]) == 1
    assert "error:" in capsys.readouterr().err


def test_committed_mapping_imports_the_flat_table_under_isco_major_groups(raw: Path, tmp_path: Path) -> None:
    source = tmp_path / "esco"
    source.mkdir()
    shutil.copy(ESCO_DIR / "esco_mapping.yaml", source)
    shutil.copy(ESCO_DIR / "isco_major_groups.csv", source)
    assert flatten_esco.main(["--raw", str(raw), "--output", str(source / "raw" / "esco_flat.csv")]) == 0

    result = import_source(source / "esco_mapping.yaml")

    # The mapping's filter, not the script, drops the optional data scientist -> statistics link.
    assert result.counts == {"categories": 2, "specialities": 2, "tags": 2, "speciality_tags": 2}
    dump = (source / "esco_taxonomy.sql").read_text(encoding="utf-8")
    assert "(1000, 'Craft and related trades workers', 2, '1')" in dump
    assert "(1001, 'Professionals', 2, '1')" in dump


def test_committed_esco_dump_loads_in_the_generalization_config() -> None:
    config = load_config(CONFIGS / "generalization_v1.yaml")
    esco = next(dataset for dataset in config.datasets if dataset.name == "esco")

    taxonomy = load_dataset(esco)

    assert len(taxonomy.categories) == 10
    assert {row["service_id"] for row in taxonomy.categories} == {2}
    linked = {link["speciality_id"] for link in taxonomy.speciality_tags}
    assert linked == {row["id"] for row in taxonomy.specialities}  # every occupation keeps an essential skill
