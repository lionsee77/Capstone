"""import-source: write a source's dump and tag context, and the next round's config from a snapshot."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from taxonomy_pipeline.app.sources import ROUND_BASELINE, import_source
from taxonomy_pipeline.config import load_config
from taxonomy_pipeline.taxonomy import load_dataset
from tests.conftest import FIXTURES

TEMPLATE = {
    "datasets": [
        {"name": "original", "type": "sql_dump", "paths": ["original.sql"]},
        {"name": "skillsfuture", "type": "sql_dump", "paths": ["skillsfuture.sql"]},
        {"name": "ssic", "type": "ssic", "paths": ["ssic.xlsx"]},
    ],
    "metrics": [
        {"id": "integrity", "params": {"dataset": "original"}},
        {"id": "integrity", "params": {"dataset": "skillsfuture"}},
        {"id": "breadth", "params": {"target": "original", "ssic_reference": "ssic"}},
        {"id": "saturation", "params": {
            "baseline": "original", "incoming": ["skillsfuture"], "threshold": 90,
            "semantic_threshold_by_level": {"specialities": 0.8},
            "judge": {"model": "m", "cache": "judge_cache.json", "levels": ["tags"]},
        }},
    ],
}


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """A flat source and its mapping, in their own folder."""
    folder = tmp_path / "demo"
    folder.mkdir()
    (folder / "flat.csv").write_text(
        "Sector,Role,Skill,Desc\nICT,Data Analyst,SQL,Query databases\nICT,Data Analyst,Python,\n,,Orphan,\n",
        encoding="utf-8",
    )
    (folder / "mapping.yaml").write_text(yaml.safe_dump({
        "name": "demo", "display_name": "Demo", "source": "flat.csv", "service_id": 1,
        "columns": {"category": "Sector", "speciality": "Role", "tag": "Skill", "tag_description": "Desc"},
    }), encoding="utf-8")
    return folder / "mapping.yaml"


@pytest.fixture
def snapshot(tmp_path: Path) -> Path:
    """An integrate-style snapshot: baseline dump copies plus additions, loaded by enriched.yaml."""
    folder = tmp_path / "integrations" / "round1"
    (folder / "baseline").mkdir(parents=True)
    shutil.copyfile(FIXTURES / "mini_original.sql", folder / "baseline" / "original.sql")
    (folder / "additions.sql").write_text("INSERT INTO `tags` (`id`, `name`) VALUES\n(104, 'audit');\n",
                                          encoding="utf-8")
    (folder / "enriched.yaml").write_text(yaml.safe_dump({
        "datasets": [{"name": "enriched", "type": "sql_dump", "paths": ["baseline/original.sql", "additions.sql"]}],
        "metrics": [{"id": "integrity", "params": {"dataset": "enriched"}}],
    }), encoding="utf-8")
    return folder


@pytest.fixture
def template(tmp_path: Path) -> Path:
    path = tmp_path / "configs" / "template.yaml"
    path.parent.mkdir()
    path.write_text(yaml.safe_dump(TEMPLATE), encoding="utf-8")
    return path


def test_writes_the_dump_and_tag_context_next_to_the_mapping(source: Path) -> None:
    result = import_source(source)

    assert result.sql_path == source.parent / "demo_taxonomy.sql" and result.sql_path.exists()
    assert result.context_path == source.parent / "demo_tag_context.json" and result.context_path.exists()
    assert result.counts == {"categories": 1, "specialities": 1, "tags": 3, "speciality_tags": 2}
    assert result.round_config is None
    assert result.warnings == [
        "retained 1 tag(s) without complete parent paths for Case B; check source data",
        "2 tag(s) have no description; Case B sees only their names",
    ]


def test_refuses_a_source_with_no_usable_rows(source: Path) -> None:
    source.with_name("flat.csv").write_text("Sector,Role,Skill,Desc\nICT,Analyst,,\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no row has a tag"):
        import_source(source)


def test_imports_a_source_containing_only_parentless_tags(source: Path) -> None:
    import json

    source.with_name("flat.csv").write_text("Sector,Role,Skill,Desc\n,,Python,Write code\n", encoding="utf-8")
    result = import_source(source)
    assert result.counts == {"categories": 0, "specialities": 0, "tags": 1, "speciality_tags": 0}
    context = json.loads(result.context_path.read_text())
    assert context["tags"] == {"3000": {"description": "Write code"}}
    assert any("without complete parent paths" in warning for warning in result.warnings)


def test_round_config_compares_the_source_against_the_snapshot_with_the_templates_metrics(
    source: Path, snapshot: Path, template: Path, tmp_path: Path,
) -> None:
    round_path = tmp_path / "configs" / "round2.yaml"
    result = import_source(source, baseline_snapshot=snapshot, template_config=template, round_config=round_path)

    assert result.round_config == round_path
    config = load_config(round_path)
    assert config.dataset_names() == [ROUND_BASELINE, "demo", "ssic"]
    baseline = load_dataset(config.datasets[0])
    assert [tag["name"] for tag in baseline.tags][-1] == "audit"  # snapshot additions are part of the baseline
    assert len(load_dataset(config.datasets[1]).tags) == 3
    assert config.datasets[2].paths == [str((template.parent / "ssic.xlsx").resolve())]

    metrics = [(metric.id, metric.params) for metric in config.metrics]
    assert metrics[:3] == [
        ("integrity", {"dataset": ROUND_BASELINE}),
        ("integrity", {"dataset": "demo"}),
        ("breadth", {"target": ROUND_BASELINE, "ssic_reference": "ssic"}),
    ]
    saturation = metrics[3][1]
    assert saturation["baseline"] == ROUND_BASELINE and saturation["incoming"] == ["demo"]
    assert saturation["semantic_threshold_by_level"] == {"specialities": 0.8}
    assert saturation["judge"]["cache"] == "judge_cache_demo.json"
    # Written relative to the config, so the folder can move with its inputs.
    assert yaml.safe_load(round_path.read_text())["datasets"][1]["paths"] == ["../demo/demo_taxonomy.sql"]


def test_two_template_incomings_collapse_into_the_one_new_source(source: Path, snapshot: Path, tmp_path: Path) -> None:
    raw = {**TEMPLATE, "datasets": [*TEMPLATE["datasets"], {"name": "onet", "paths": ["onet.sql"]}]}
    raw["metrics"] = [
        {"id": "integrity", "params": {"dataset": "skillsfuture"}},
        {"id": "integrity", "params": {"dataset": "onet"}},
        {"id": "saturation", "params": {"baseline": "original", "incoming": ["skillsfuture", "onet"]}},
    ]
    template = tmp_path / "two.yaml"
    template.write_text(yaml.safe_dump(raw), encoding="utf-8")

    result = import_source(source, baseline_snapshot=snapshot, template_config=template,
                           round_config=tmp_path / "round.yaml")
    config = load_config(result.round_config)
    assert [(m.id, m.params) for m in config.metrics] == [
        ("integrity", {"dataset": "demo"}),
        ("saturation", {"baseline": ROUND_BASELINE, "incoming": ["demo"]}),
    ]


def test_refuses_to_overwrite_an_existing_round_config_before_writing_anything(
    source: Path, snapshot: Path, template: Path, tmp_path: Path,
) -> None:
    round_path = tmp_path / "round.yaml"
    round_path.write_text("keep me", encoding="utf-8")
    with pytest.raises(ValueError, match="already exists"):
        import_source(source, baseline_snapshot=snapshot, template_config=template, round_config=round_path)
    assert round_path.read_text() == "keep me"
    assert not source.with_name("demo_taxonomy.sql").exists()


@pytest.mark.parametrize("name", ["ssic", ROUND_BASELINE])
def test_refuses_a_source_named_like_a_dataset_the_round_config_keeps(
    source: Path, snapshot: Path, template: Path, tmp_path: Path, name: str,
) -> None:
    raw = yaml.safe_load(source.read_text())
    source.write_text(yaml.safe_dump({**raw, "name": name}), encoding="utf-8")
    with pytest.raises(ValueError, match=f"source name '{name}' is already a dataset in the round config"):
        import_source(source, baseline_snapshot=snapshot, template_config=template,
                      round_config=tmp_path / "round.yaml")
    assert not (tmp_path / "round.yaml").exists()
    assert not source.with_name(f"{name}_taxonomy.sql").exists()


def test_refuses_a_folder_that_is_not_an_integrate_snapshot_before_writing_anything(
    source: Path, template: Path, tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="not an integrate snapshot"):
        import_source(source, baseline_snapshot=tmp_path, template_config=template,
                      round_config=tmp_path / "round.yaml")
    assert not (tmp_path / "round.yaml").exists()
    assert not source.with_name("demo_taxonomy.sql").exists()


def test_removes_a_round_config_that_fails_validation_so_a_rerun_is_not_blocked(
    source: Path, snapshot: Path, tmp_path: Path,
) -> None:
    template = tmp_path / "bad.yaml"
    template.write_text(yaml.safe_dump({**TEMPLATE, "metric_definitions_version": 0}), encoding="utf-8")
    round_path = tmp_path / "round.yaml"
    with pytest.raises(ValueError, match="metric_definitions_version"):
        import_source(source, baseline_snapshot=snapshot, template_config=template, round_config=round_path)
    assert not round_path.exists()


def test_refuses_a_template_without_one_saturation_metric(source: Path, snapshot: Path, tmp_path: Path) -> None:
    template = tmp_path / "plain.yaml"
    template.write_text(yaml.safe_dump({**TEMPLATE, "metrics": TEMPLATE["metrics"][:1]}), encoding="utf-8")
    with pytest.raises(ValueError, match="exactly one saturation metric"):
        import_source(source, baseline_snapshot=snapshot, template_config=template,
                      round_config=tmp_path / "round.yaml")


def test_warns_about_code_prefixes_the_labels_file_does_not_name(tmp_path: Path) -> None:
    (tmp_path / "flat.csv").write_text("Code,Role,Skill\n15-1,Analyst,SQL\n99-1,Mystery,Excel\n", encoding="utf-8")
    (tmp_path / "soc.csv").write_text("code,label\n15,Computer and Mathematical\n", encoding="utf-8")
    (tmp_path / "mapping.yaml").write_text(yaml.safe_dump({
        "name": "demo", "display_name": "Demo", "source": "flat.csv", "service_id": 2,
        "columns": {"category": {"column": "Code", "prefix": 2, "labels": "soc.csv"}, "speciality": "Role",
                    "tag": "Skill"},
    }), encoding="utf-8")

    result = import_source(tmp_path / "mapping.yaml")
    assert result.counts["categories"] == 1
    assert "no label for code prefix(es) ['99']" in " ".join(result.warnings)


def test_warns_when_tag_spellings_are_merged_for_mysql(tmp_path: Path) -> None:
    (tmp_path / "flat.csv").write_text("Sector,Role,Skill\nICT,Analyst,Python\nICT,Engineer,python\n", encoding="utf-8")
    (tmp_path / "mapping.yaml").write_text(yaml.safe_dump({
        "name": "demo", "display_name": "Demo", "source": "flat.csv", "service_id": 1,
        "columns": {"category": "Sector", "speciality": "Role", "tag": "Skill"},
    }), encoding="utf-8")

    result = import_source(tmp_path / "mapping.yaml")
    assert result.counts["tags"] == 1
    assert "merged 1 tag(s) whose spellings MySQL treats as one name (case/accents): 'Python' <- ['python']" in (
        " ".join(result.warnings))
