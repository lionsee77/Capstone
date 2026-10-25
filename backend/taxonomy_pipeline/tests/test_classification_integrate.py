"""Integrate: a reviewed merge run becomes an enriched snapshot, and an unreviewed or stale one is refused."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from taxonomy_pipeline.classification import integrate as integrate_module
from taxonomy_pipeline.classification.integrate import IntegrationError, _additions_sql, integrate
from taxonomy_pipeline.classification.models import ResolutionReport, ReviewDecision, TaxonomyPath
from taxonomy_pipeline.classification.runner import load_decisions, run_merge_preview
from taxonomy_pipeline.config import DatasetConfig, MetricConfig, PipelineConfig, load_config
from taxonomy_pipeline.datasets.base import CONCEPTS, TaxonomyDataset
from taxonomy_pipeline.datasets.sql_dump import SqlDumpSource, iter_insert_statements
from taxonomy_pipeline.pipeline import load_datasets

FIXTURES = Path(__file__).parent / "fixtures" / "classification"
PRODUCER_FILES = [FIXTURES / "case_a.json", FIXTURES / "case_b.json"]


def _literal(value: object) -> str:
    if isinstance(value, int):
        return str(value)
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


@pytest.fixture
def baseline(tmp_path: Path) -> DatasetConfig:
    """The classification fixture taxonomy, written as a SQL dump like the client's."""
    taxonomy = json.loads((FIXTURES / "taxonomy.json").read_text(encoding="utf-8"))
    statements = []
    for table in CONCEPTS:
        rows = taxonomy[table]
        columns = list(rows[0])
        values = ",\n".join("(" + ", ".join(_literal(row[c]) for c in columns) + ")" for row in rows)
        statements.append(f"INSERT INTO `{table}` ({', '.join(f'`{c}`' for c in columns)}) VALUES\n{values};")
    path = tmp_path / "baseline.sql"
    path.write_text("\n\n".join(statements) + "\n", encoding="utf-8")
    return DatasetConfig(name=taxonomy["name"], paths=[str(path)])


def _decisions(*, merge_homonyms: bool = True) -> list[ReviewDecision]:
    """The fixture's review decisions. They keep the programming language and the animal as two new
    tags both named "Python", which the client's UNIQUE `tags.name` can't hold. By default the two are
    combined into one `create` decision, as a reviewer would have to: one "Python" tag linked under
    both specialities."""
    decisions = load_decisions(FIXTURES / "review_decisions.json")
    if not merge_homonyms:
        return decisions
    homonym_sources = {("plain-skills", "46"), ("plain-skills", "47")}  # both labelled "Python"
    pythons = [d for d in decisions if {(m.source_dataset, m.tag_id) for m in d.members} <= homonym_sources]
    assert len(pythons) == 2
    combined = ReviewDecision(
        members=[m for d in pythons for m in d.members], action="create",
        approved_paths=[path for d in pythons for path in d.approved_paths],
        reason="One shared keyword, linked under both specialities.",
    )
    return [d for d in decisions if d not in pythons] + [combined]


def _resolution(
    tmp_path: Path, baseline: DatasetConfig, *, decisions: list[ReviewDecision] | None = None,
) -> ResolutionReport:
    taxonomy = SqlDumpSource(baseline.name, baseline.paths).load()
    run = run_merge_preview(
        PRODUCER_FILES, taxonomy, out_dir=tmp_path / "merge",
        decisions=_decisions() if decisions is None else decisions,
    )
    return ResolutionReport.model_validate_json((run.out_dir / "resolution.json").read_text(encoding="utf-8"))


def test_writes_a_snapshot_of_baseline_plus_reviewed_additions(tmp_path, baseline):
    result = integrate(_resolution(tmp_path, baseline), baseline, out_dir=tmp_path / "out")

    out = tmp_path / "out"
    assert sorted(p.name for p in out.iterdir()) == ["additions.sql", "baseline", "enriched.yaml", "manifest.json"]
    assert (out / "baseline" / "baseline.sql").read_bytes() == Path(baseline.paths[0]).read_bytes()
    # "Data visualization" and one shared "Python", each linked.
    assert result.counts["tags"] == 2
    assert result.counts["speciality_tags"] >= 2

    enriched = load_datasets(load_config(out / "enriched.yaml"))["enriched"]
    original = SqlDumpSource(baseline.name, baseline.paths).load()
    for concept in CONCEPTS:
        rows = getattr(enriched, concept)
        assert rows[: len(getattr(original, concept))] == getattr(original, concept)  # baseline rows untouched
    new_tags = enriched.tags[len(original.tags):]
    assert [tag["id"] for tag in new_tags] == [201, 202]  # continue after the baseline's max tag ID
    assert {tag["name"] for tag in new_tags} == {"Data visualization", "Python"}
    python = next(tag["id"] for tag in new_tags if tag["name"] == "Python")
    linked = {link["speciality_id"] for link in enriched.speciality_tags if link["tag_id"] == python}
    assert linked == {122, 220}  # Programming and Zoology share the one keyword
    new_specialities = enriched.specialities[len(original.specialities):]
    assert all(row["status"] == "1" and row["id"] > 220 for row in new_specialities)


def test_new_links_point_only_at_real_rows(tmp_path, baseline):
    integrate(_resolution(tmp_path, baseline), baseline, out_dir=tmp_path / "out")
    enriched = load_datasets(load_config(tmp_path / "out" / "enriched.yaml"))["enriched"]

    speciality_ids = {row["id"] for row in enriched.specialities}
    tag_ids = {row["id"] for row in enriched.tags}
    assert all(isinstance(row["id"], int) for concept in CONCEPTS for row in getattr(enriched, concept))
    assert all(link["speciality_id"] in speciality_ids and link["tag_id"] in tag_ids
               for link in enriched.speciality_tags)


def test_rerunning_the_same_merge_run_gives_identical_ids(tmp_path, baseline):
    resolution = _resolution(tmp_path, baseline)
    first = integrate(resolution, baseline, out_dir=tmp_path / "first")
    second = integrate(resolution, baseline, out_dir=tmp_path / "second")

    assert (tmp_path / "first" / "additions.sql").read_text() == (tmp_path / "second" / "additions.sql").read_text()
    assert first.manifest["id_map"] == second.manifest["id_map"]
    assert first.manifest["enriched_snapshot_id"] == second.manifest["enriched_snapshot_id"]


def test_manifest_records_where_the_snapshot_came_from(tmp_path, baseline):
    resolution = _resolution(tmp_path, baseline)
    manifest = integrate(
        resolution, baseline, out_dir=tmp_path / "out", provenance={"saturation_run_ids": ["run-1"]},
    ).manifest

    assert manifest["baseline"]["snapshot_id"] == resolution.baseline_snapshot_id
    assert manifest["merge_run"] == {"saturation_run_ids": ["run-1"]}
    assert manifest["decisions"]["count"] == len(resolution.decisions)
    assert manifest["inputs"] == ["case_a.json", "case_b.json"]
    assert manifest["id_ranges"]["tags"] == [201, 202]
    assert manifest["omitted_records"] == resolution.evidence.omitted_counts
    assert set(manifest["exact_duplicate_rows"]) == {"categories", "specialities", "tags"}
    assert all(set(rows) == {"baseline", "enriched"} for rows in manifest["exact_duplicate_rows"].values())


def test_refuses_while_sources_are_pending_review(tmp_path, baseline):
    resolution = _resolution(tmp_path, baseline, decisions=[])

    with pytest.raises(IntegrationError, match=r"still pending review .*rerun merge-preview with --decisions"):
        integrate(resolution, baseline, out_dir=tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_refuses_when_the_baseline_changed_since_the_merge_run(tmp_path, baseline):
    resolution = _resolution(tmp_path, baseline)
    path = Path(baseline.paths[0])
    path.write_text(path.read_text() + "\nINSERT INTO `tags` (`id`, `name`) VALUES (999, 'Added later');\n")

    with pytest.raises(IntegrationError, match="Rerun merge-preview against the current baseline"):
        integrate(resolution, baseline, out_dir=tmp_path / "out")


def test_refuses_to_overwrite_an_existing_snapshot(tmp_path, baseline):
    (tmp_path / "out").mkdir()
    with pytest.raises(IntegrationError, match="already exists"):
        integrate(_resolution(tmp_path, baseline), baseline, out_dir=tmp_path / "out")


def test_writes_nothing_if_the_result_would_add_integrity_problems(tmp_path, baseline, monkeypatch):
    real_assign = integrate_module._assign_ids

    def drop_links(combined: TaxonomyDataset, original: TaxonomyDataset) -> tuple[dict, dict]:
        additions, id_map = real_assign(combined, original)
        return {**additions, "speciality_tags": []}, id_map  # new tags and specialities left unlinked

    monkeypatch.setattr(integrate_module, "_assign_ids", drop_links)
    with pytest.raises(IntegrationError, match="introduce integrity problems"):
        integrate(_resolution(tmp_path, baseline), baseline, out_dir=tmp_path / "out")
    assert not (tmp_path / "out").exists()
    assert not any(p.name.startswith(".out.") for p in tmp_path.iterdir())  # staging folder cleaned up


def test_refuses_a_new_tag_the_unique_name_key_would_reject(tmp_path, baseline, monkeypatch):
    real_assign = integrate_module._assign_ids

    def rename_to_existing(combined: TaxonomyDataset, original: TaxonomyDataset) -> tuple[dict, dict]:
        additions, id_map = real_assign(combined, original)
        additions["tags"][0]["name"] = "sql"  # baseline has "SQL"; utf8mb4_general_ci ignores case
        return additions, id_map

    monkeypatch.setattr(integrate_module, "_assign_ids", rename_to_existing)
    with pytest.raises(IntegrationError, match=r"new tag 'sql' .*collides with existing tag 'SQL'.*reuse"):
        integrate(_resolution(tmp_path, baseline), baseline, out_dir=tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_refuses_two_new_tags_with_the_same_name(tmp_path, baseline):
    resolution = _resolution(tmp_path, baseline, decisions=_decisions(merge_homonyms=False))

    shared_name = r"new tags 'Python' .* and 'Python' .* share a name.*one `create` decision"
    with pytest.raises(IntegrationError, match=shared_name):
        integrate(resolution, baseline, out_dir=tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_additions_sql_sets_the_mode_its_escaping_needs(tmp_path, baseline):
    integrate(_resolution(tmp_path, baseline), baseline, out_dir=tmp_path / "out")
    sql = (tmp_path / "out" / "additions.sql").read_text(encoding="utf-8")
    assert sql.index('SET SQL_MODE = "NO_AUTO_VALUE_ON_ZERO";') < sql.index("INSERT INTO")


def test_additions_round_trip_awkward_names_through_the_sql_parser():
    names = ["O'Reilly Media", "Back\\slash", "Line\nbreak", 'Say "hi"', "Data & AI (2026); v2"]
    additions = {
        "categories": [], "specialities": [], "speciality_tags": [],
        "tags": [{"id": index, "name": name} for index, name in enumerate(names, start=1)],
    }
    sql = _additions_sql(additions, {c: c for c in ("categories", "specialities", "tags", "speciality_tags")}, "x")

    [(table, rows)] = list(iter_insert_statements(sql))
    assert table == "tags" and rows == additions["tags"]


def test_refuses_flagged_review_candidates_until_a_reviewer_approves_one_path(tmp_path, baseline):
    from tests.test_classification_runner import approve_data_analytics, review_candidate_file

    taxonomy = SqlDumpSource(baseline.name, baseline.paths).load()
    inputs = [review_candidate_file(tmp_path)]

    def merge(name: str, decisions: list[ReviewDecision]) -> ResolutionReport:
        run = run_merge_preview(inputs, taxonomy, out_dir=tmp_path / name, decisions=decisions)
        return ResolutionReport.model_validate_json((run.out_dir / "resolution.json").read_text(encoding="utf-8"))

    with pytest.raises(IntegrationError, match=r"1 source tag\(s\) are still pending review \(case-a:9\)"):
        integrate(merge("unreviewed", []), baseline, out_dir=tmp_path / "refused")
    assert not (tmp_path / "refused").exists()

    result = integrate(merge("reviewed", [approve_data_analytics()]), baseline, out_dir=tmp_path / "out")
    enriched = load_datasets(load_config(tmp_path / "out" / "enriched.yaml"))["enriched"]
    categories = {row["id"]: row["name"] for row in enriched.categories}
    fire = [row for row in enriched.specialities if row["name"] == "Fire Prevention"]
    assert [categories[row["category_id"]] for row in fire] == ["Data analytics"]  # the approved candidate only
    assert {row["name"] for row in enriched.tags[len(taxonomy.tags):]} == {"Fire Prevention", "Spreadsheet Modelling"}
    assert result.counts["tags"] == 2


def test_a_reviewer_can_map_an_ambiguous_tag_onto_the_existing_tag_it_resembles(tmp_path, baseline):
    # What Case A/B emit for a REVIEW_AMBIGUOUS tag: a placement, flagged, naming the close baseline tag.
    path = tmp_path / "ambiguous.json"
    path.write_text(json.dumps([{
        "source_dataset": "skillsfuture", "tag_id": "77", "tag": "Structured Query Language",
        "original_classification": "classified", "service": "Subject", "category": "Data analytics",
        "speciality": "Data presentation", "confidence": 0.6, "needs_review": True,
        "reason": "Proposed. Needs review: saturation could not confirm this tag is new. It is close to existing "
                  "tag 'SQL' in meaning (0.68).",
    }]), encoding="utf-8")
    taxonomy = SqlDumpSource(baseline.name, baseline.paths).load()
    reuse_sql = ReviewDecision(
        members=[{"source_dataset": "skillsfuture", "tag_id": "77"}], action="reuse", existing_tag_id=200,
        approved_paths=[TaxonomyPath(service="Subject", category="Data analytics", speciality="Data presentation")],
        reason="Same skill as SQL.",
    )

    unreviewed = run_merge_preview([path], taxonomy, out_dir=tmp_path / "unreviewed")
    assert unreviewed.preview.counts.pending_sources == 1
    run = run_merge_preview([path], taxonomy, out_dir=tmp_path / "reviewed", decisions=[reuse_sql])
    resolution = ResolutionReport.model_validate_json((run.out_dir / "resolution.json").read_text(encoding="utf-8"))
    result = integrate(resolution, baseline, out_dir=tmp_path / "out")

    assert result.counts["tags"] == 0  # no new tag: the skill is SQL
    enriched = load_datasets(load_config(tmp_path / "out" / "enriched.yaml"))["enriched"]
    assert {(link["speciality_id"], link["tag_id"]) for link in enriched.speciality_tags} == {(122, 200), (120, 200)}


def test_enriched_config_falls_back_to_integrity_and_depth_without_scoring_metrics(tmp_path, baseline):
    # A round config that only places tags: nothing in it scores the baseline.
    scoring = PipelineConfig(datasets=[baseline], metrics=[
        MetricConfig(id="category_adaptation", params={"baseline": baseline.name, "incoming": "x"}),
    ])
    integrate(_resolution(tmp_path, baseline), baseline, out_dir=tmp_path / "out", scoring_config=scoring)

    config = load_config(tmp_path / "out" / "enriched.yaml")
    assert [(m.id, m.params) for m in config.metrics] == [
        ("integrity", {"dataset": "enriched"}), ("depth", {"dataset": "enriched"}),
    ]
    assert [d.name for d in config.datasets] == ["enriched"]


@pytest.mark.parametrize("pin", [1, None])
def test_the_enriched_config_keeps_the_round_configs_metric_definitions_pin(tmp_path, baseline, pin):
    # A pinned round config (configs/baseline_v<N>.yaml) holds the snapshot's scoring to the same version.
    scoring = PipelineConfig(metric_definitions_version=pin, datasets=[baseline], metrics=[
        MetricConfig(id="integrity", params={"dataset": baseline.name}),
    ])
    integrate(_resolution(tmp_path, baseline), baseline, out_dir=tmp_path / "out", scoring_config=scoring)

    assert load_config(tmp_path / "out" / "enriched.yaml").metric_definitions_version == pin


def test_the_snapshot_never_shares_a_name_with_a_dataset_its_metrics_read(tmp_path, baseline):
    # The round compares against an earlier snapshot that is itself called "enriched".
    earlier = DatasetConfig(name="enriched", paths=baseline.paths)
    scoring = PipelineConfig(datasets=[baseline, earlier], metrics=[
        MetricConfig(id="saturation", params={"baseline": baseline.name, "incoming": ["enriched"]}),
    ])
    integrate(_resolution(tmp_path, baseline), baseline, out_dir=tmp_path / "out", scoring_config=scoring)

    config = load_config(tmp_path / "out" / "enriched.yaml")
    assert [d.name for d in config.datasets] == ["enriched_2", "enriched"]
    assert [(m.id, m.params) for m in config.metrics] == [
        ("integrity", {"dataset": "enriched_2"}), ("depth", {"dataset": "enriched_2"}),
        ("saturation", {"baseline": "enriched_2", "incoming": ["enriched"]}),
    ]
    assert config.datasets[0].paths[-1].endswith("additions.sql")  # the snapshot, not the earlier one
