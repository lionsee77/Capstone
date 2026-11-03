from __future__ import annotations

from taxonomy_pipeline import db
from taxonomy_pipeline.app.lineage import taxonomy_lineage
from taxonomy_pipeline.models import RunReport

BASELINE = {"tags.sql": "INSERT INTO tags VALUES (1,'Python');\n"}


def _two_rounds(tmp_path, integration_round):
    first = integration_round("round-1", BASELINE, "INSERT INTO tags VALUES (2,'Rust');\n")
    second = integration_round(
        "round-2", {"tags.sql": BASELINE["tags.sql"], "additions.sql": (first / "additions.sql").read_text()},
        "INSERT INTO tags VALUES (3,'Go');\n", source="onet", dataset="round_baseline",
        created_at="2026-10-31T10:00:00+00:00",
    )
    return first, second


def _save_run(run_id, created_at, datasets, *, judged=False):
    """Store a finished run whose config scores `datasets` (name -> files), judged or not."""
    import json

    saturation = {"id": "saturation", "params": {"judge": {"model": "m"} if judged else None}}
    config = {"datasets": [{"name": name, "paths": [str(path) for path in paths]} for name, paths in datasets.items()],
              "metrics": [saturation]}
    report = RunReport(run_id=run_id, status="done")
    db.save_run(report, config_json=json.dumps(config))
    with db.Session(db.get_engine()) as session:
        record = session.get(db.RunRecord, run_id)
        record.created_at = created_at
        session.add(record)
        session.commit()


def _stage_files(first, second):
    return {
        "root": [first / "baseline" / "tags.sql"],
        "round-1": [first / "baseline" / "tags.sql", first / "additions.sql"],
        "round-2": [second / "baseline" / "tags.sql", second / "baseline" / "additions.sql", second / "additions.sql"],
    }


def test_chains_rounds_from_the_root_baseline_in_order(tmp_path, integration_round):
    _two_rounds(tmp_path, integration_round)

    lineage = taxonomy_lineage(tmp_path / "integrations", tmp_path / "merge_runs")

    stages = {stage.id: stage for stage in lineage.stages}
    assert stages["baseline:original"].parent is None and stages["baseline:original"].name == "original"
    assert stages["round-1"].parent == "baseline:original" and stages["round-1"].sources == ["skillsfuture"]
    assert stages["round-2"].parent == "round-1" and stages["round-2"].sources == ["onet"]
    assert [chain.stages for chain in lineage.chains] == [["baseline:original", "round-1", "round-2"]]


def test_branches_become_separate_chains_longest_first(tmp_path, integration_round):
    _two_rounds(tmp_path, integration_round)
    integration_round(
        "trial", BASELINE, "INSERT INTO tags VALUES (9,'Trial');\n", created_at="2026-10-01T00:00:00+00:00",
    )

    lineage = taxonomy_lineage(tmp_path / "integrations", tmp_path / "merge_runs")

    assert [chain.stages for chain in lineage.chains] == [
        ["baseline:original", "round-1", "round-2"], ["baseline:original", "trial"],
    ]


def test_links_a_run_to_stages_by_file_contents(tmp_path, integration_round):
    first, second = _two_rounds(tmp_path, integration_round)
    files = _stage_files(first, second)
    original = tmp_path / "original" / "tags.sql"  # the dump the first round copied, scored from where it lives
    original.parent.mkdir()
    original.write_text(BASELINE["tags.sql"], encoding="utf-8")
    _save_run("all-stages", "2026-11-01T00:00:00+00:00",
              {"b1": [original], "b2": files["round-1"], "b3": files["round-2"], "ref": [tmp_path / "x.xlsx"]})

    lineage = taxonomy_lineage(tmp_path / "integrations", tmp_path / "merge_runs")

    [run] = lineage.runs
    assert run.datasets == {"baseline:original": "b1", "round-1": "b2", "round-2": "b3"}
    assert lineage.chains[0].runs == ["all-stages"]


def test_ranks_runs_by_stages_covered_then_judge_then_newest(tmp_path, integration_round):
    first, second = _two_rounds(tmp_path, integration_round)
    files = _stage_files(first, second)
    every = {"b1": files["root"], "b2": files["round-1"], "b3": files["round-2"]}
    _save_run("all-unjudged-new", "2026-11-03T00:00:00+00:00", every)
    _save_run("all-judged-old", "2026-11-01T00:00:00+00:00", every, judged=True)
    _save_run("all-judged-new", "2026-11-02T00:00:00+00:00", every, judged=True)
    _save_run("two-judged", "2026-11-04T00:00:00+00:00", {"b2": files["round-1"], "b3": files["round-2"]}, judged=True)
    _save_run("one-stage", "2026-11-05T00:00:00+00:00", {"b3": files["round-2"]}, judged=True)

    lineage = taxonomy_lineage(tmp_path / "integrations", tmp_path / "merge_runs")

    assert lineage.chains[0].runs == ["all-judged-new", "all-judged-old", "all-unjudged-new", "two-judged"]
    assert {run.run_id: run.judged for run in lineage.runs}["all-unjudged-new"] is False
    assert "one-stage" in {run.run_id for run in lineage.runs}


def test_ignores_unfinished_runs_and_runs_scoring_no_stage(tmp_path, integration_round):
    first, second = _two_rounds(tmp_path, integration_round)
    db.save_run(RunReport(run_id="queued", status="queued"), config_json='{"datasets": [], "metrics": []}')
    other = tmp_path / "other.sql"
    other.write_text("unrelated", encoding="utf-8")
    _save_run("unrelated", "2026-11-01T00:00:00+00:00", {"x": [other]})

    assert taxonomy_lineage(tmp_path / "integrations", tmp_path / "merge_runs").runs == []


def test_no_rounds_gives_an_empty_lineage(tmp_path):
    lineage = taxonomy_lineage(tmp_path / "integrations", tmp_path / "merge_runs")

    assert lineage.stages == [] and lineage.chains == [] and lineage.runs == []
