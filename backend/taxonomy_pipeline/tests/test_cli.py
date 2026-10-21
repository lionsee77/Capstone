from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from taxonomy_pipeline.cli import main
from taxonomy_pipeline.definitions import METRIC_DEFINITIONS_VERSION
from tests.conftest import FIXTURES


@pytest.mark.parametrize(("lexical", "pending"), [(0.9, 1), (1.0, 0)])
# CLI thresholds must reach screening, change review outcomes and survive saved report replay.
def test_merge_preview_exposes_and_persists_screening_thresholds(
    tmp_path: Path, lexical: float, pending: int,
) -> None:
    config = _write_stage_config(tmp_path)
    producer = tmp_path / "producer.json"
    producer.write_text(json.dumps([{
        "source_dataset": "skillsfuture", "tag_id": "9001", "tag": "Campaign planning",
        "original_classification": "classified", "service": "Industry", "category": "Marketing",
        "speciality": "Brand Strateg", "confidence": 0.8, "reason": "Proposed placement",
    }]), encoding="utf-8")
    output = tmp_path / "merge"
    assert main(["merge-preview", "--config", str(config), "--input", str(producer),
                 "--out-dir", str(output), "--screening-lexical", str(lexical), "--screening-semantic", "0.85"]) == 0
    resolution = json.loads((output / "resolution.json").read_text(encoding="utf-8"))
    assert resolution["screening"]["settings"] == {"lexical_threshold": lexical, "semantic_threshold": 0.85}
    assert resolution["counts"]["pending_sources"] == pending


@pytest.mark.parametrize(("flag", "value"), [("--screening-lexical", "1.1"), ("--screening-semantic", "nan")])
# Invalid CLI thresholds must fail before reading inputs or creating reports.
def test_merge_preview_rejects_invalid_screening_thresholds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], flag: str, value: str,
) -> None:
    output = tmp_path / "merge"
    assert main(["merge-preview", "--input", "unused.json", "--out-dir", str(output), flag, value]) == 1
    assert "threshold" in capsys.readouterr().err
    assert not output.exists()


def _write_config(tmp_path, metrics):
    config = {
        "datasets": [{"name": "original", "paths": [str(FIXTURES / "mini_original.sql")]}],
        "metrics": metrics,
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config))
    return config_path


def test_exit_code_zero_when_every_metric_succeeds(tmp_path, isolated_db, capsys):
    config_path = _write_config(tmp_path, [{"id": "integrity", "params": {"dataset": "original"}}])
    code = main(["run", "--config", str(config_path)])
    assert code == 0


def test_exit_code_nonzero_when_a_metric_errors(tmp_path, isolated_db, capsys):
    # valid pipeline run (report.status == "done"), but one metric id doesn't
    # exist - this must still fail the CLI, not just log a warning.
    config_path = _write_config(
        tmp_path,
        [
            {"id": "integrity", "params": {"dataset": "original"}},
            {"id": "not_a_real_metric"},
        ],
    )
    code = main(["run", "--config", str(config_path)])
    assert code != 0


BASELINE_SQL = """
INSERT INTO `services` (`id`, `name`) VALUES (1, 'Industry');
INSERT INTO `categories` (`id`, `name`, `service_id`) VALUES (1, 'Marketing', 1);
INSERT INTO `specialities` (`id`, `name`, `category_id`) VALUES (10, 'Brand Strategy', 1);
INSERT INTO `tags` (`id`, `name`) VALUES (100, 'branding');
INSERT INTO `speciality_tags` (`id`, `speciality_id`, `tag_id`) VALUES (1000, 10, 100);
"""
INCOMING_SQL = """
INSERT INTO categories (id,name,service_id) VALUES (501,'Marketing',1);
INSERT INTO specialities (id,name,category_id) VALUES (601,'Brand Strategy',501);
INSERT INTO tags (id,name) VALUES (9001,'campaign planning'),(9002,'stage lighting');
INSERT INTO speciality_tags (id,speciality_id,tag_id) VALUES (701,601,9001);
"""


def _write_stage_config(tmp_path):
    (tmp_path / "baseline.sql").write_text(BASELINE_SQL)
    (tmp_path / "incoming.sql").write_text(INCOMING_SQL)
    config = {
        "datasets": [
            {"name": "original", "paths": ["baseline.sql"]},
            {"name": "skillsfuture", "paths": ["incoming.sql"]},
            {"name": "ssic", "type": "ssic", "paths": ["not-needed-by-these-commands.xlsx"]},
        ],
        "metrics": [],
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config))
    return config_path


def test_case_a_then_merge_preview_run_end_to_end_from_the_cli(tmp_path, saturation_report, capsys):
    config_path = _write_stage_config(tmp_path)
    report = saturation_report([
        {"recommended_action": "ADD_CANDIDATE", "parent_status": "HAS_PARENT", "incoming_ids": [9001]},
        {"recommended_action": "ADD_CANDIDATE", "parent_status": "NO_PARENT", "incoming_ids": [9002]},
    ])
    case_a_output = tmp_path / "case_a_output.json"

    assert main(["case-a", "--config", str(config_path), "--report", str(report), "--semantic-model", "",
                 "--output", str(case_a_output)]) == 0
    records = json.loads(case_a_output.read_text())
    assert [(record["tag_id"], record["category"], record["speciality"]) for record in records] == [
        ("9001", "Marketing", "campaign planning"),  # the skill itself becomes the proposed speciality
    ]

    out_dir = tmp_path / "merge"
    assert main(["merge-preview", "--config", str(config_path), "--input", str(case_a_output),
                 "--out-dir", str(out_dir)]) == 0
    assert (out_dir / "preview.csv").exists()
    assert "1 new tags" in capsys.readouterr().err

    snapshot = tmp_path / "enriched"
    assert main(["integrate", "--config", str(config_path), "--merge-run", str(out_dir),
                 "--out-dir", str(snapshot)]) == 0
    assert "1 new tags" in capsys.readouterr().err
    manifest = json.loads((snapshot / "manifest.json").read_text())
    assert manifest["merge_run"]["saturation_run_ids"] == [records[0]["saturation_run_id"]]
    # The snapshot's own config scores the enriched taxonomy.
    assert main(["run", "--config", str(snapshot / "enriched.yaml"), "--no-save"]) == 0


def test_integrate_refuses_a_merge_run_with_nothing_to_add(tmp_path, capsys):
    from tests.test_classification_integrate import PRODUCER_FILES

    # These producer files target another baseline, so validation sets every record aside.
    config_path = _write_stage_config(tmp_path)
    out_dir = tmp_path / "merge"
    main(["merge-preview", "--config", str(config_path), "--out-dir", str(out_dir),
          *[arg for path in PRODUCER_FILES for arg in ("--input", str(path))]])
    capsys.readouterr()

    assert main(["integrate", "--config", str(config_path), "--merge-run", str(out_dir),
                 "--out-dir", str(tmp_path / "enriched")]) == 1
    assert "Nothing to integrate" in capsys.readouterr().err
    assert not (tmp_path / "enriched").exists()


def test_integrate_fails_cleanly_without_a_merge_run(tmp_path, capsys):
    assert main(["integrate", "--config", str(_write_stage_config(tmp_path)),
                 "--merge-run", str(tmp_path / "missing")]) == 1
    assert "[FAILED] integrate" in capsys.readouterr().err


def test_case_a_fails_cleanly_without_a_saturation_report(tmp_path, capsys):
    config_path = _write_stage_config(tmp_path)

    code = main(["case-a", "--config", str(config_path), "--report", str(tmp_path / "missing.json")])

    assert code == 1
    assert "taxonomy-pipeline run" in capsys.readouterr().err


def test_case_b_fails_cleanly_for_an_unknown_dataset(tmp_path, capsys):
    config_path = _write_stage_config(tmp_path)

    code = main(["case-b", "--config", str(config_path), "--incoming", "nope", "--report", "r.json"])

    assert code == 1
    assert "no taxonomy dataset named 'nope'" in capsys.readouterr().err


def test_merge_preview_rejects_outputs_from_different_saturation_runs(tmp_path, capsys):
    config_path = _write_stage_config(tmp_path)
    row = {
        "source_dataset": "skillsfuture", "tag_id": "9002", "tag": "stage lighting",
        "original_classification": "unclassified", "service": None, "category": None, "speciality": None,
        "confidence": None, "reason": "Unresolved",
    }
    inputs = []
    for run_id in ("run-1", "run-2"):
        path = tmp_path / f"{run_id}.json"
        path.write_text(json.dumps([{**row, "tag_id": run_id, "saturation_run_id": run_id}]))
        inputs += ["--input", str(path)]

    code = main(["merge-preview", "--config", str(config_path), *inputs, "--out-dir", str(tmp_path / "m")])

    assert code == 1
    assert "different saturation runs" in capsys.readouterr().err


def test_merge_preview_requires_an_input(tmp_path, capsys):
    code = main(["merge-preview", "--config", str(_write_stage_config(tmp_path))])

    assert code == 1
    assert "at least one --input" in capsys.readouterr().err


def _ambiguous_merge_run(tmp_path, saturation_report, run_id="run-1"):
    config_path = _write_stage_config(tmp_path)
    report = saturation_report([{
        "recommended_action": "REVIEW_AMBIGUOUS", "parent_status": "HAS_PARENT", "incoming_ids": [9001],
        "reason": "partial_similarity", "closest_baseline_name": "branding", "closest_baseline_score": 40.0,
        "closest_baseline_level": "tags",
    }], run_id=run_id)
    case_a_output = tmp_path / "case_a_output.json"
    main(["case-a", "--config", str(config_path), "--report", str(report), "--semantic-model", "",
          "--output", str(case_a_output)])
    main(["merge-preview", "--config", str(config_path), "--input", str(case_a_output),
          "--out-dir", str(tmp_path / "merge")])
    return config_path, report, case_a_output


def test_bulk_decide_settles_ambiguous_tags_so_integrate_can_run(tmp_path, saturation_report, capsys):
    config_path, report, case_a_output = _ambiguous_merge_run(tmp_path, saturation_report)
    assert "1 sources pending review" in capsys.readouterr().err

    decisions = tmp_path / "decisions.json"
    assert main(["bulk-decide", "--config", str(config_path), "--merge-run", str(tmp_path / "merge"),
                 "--report", str(report), "--create-below", "0.5", "--output", str(decisions)]) == 0
    assert "drafted 1 create and 0 reuse" in capsys.readouterr().err
    assert [decision["action"] for decision in json.loads(decisions.read_text())] == ["create"]

    main(["merge-preview", "--config", str(config_path), "--input", str(case_a_output),
          "--decisions", str(decisions), "--out-dir", str(tmp_path / "reviewed")])
    assert "0 sources pending review" in capsys.readouterr().err
    assert main(["integrate", "--config", str(config_path), "--merge-run", str(tmp_path / "reviewed"),
                 "--out-dir", str(tmp_path / "enriched")]) == 0


def test_bulk_decide_refuses_a_report_from_another_saturation_run(tmp_path, saturation_report, capsys):
    config_path, _, _ = _ambiguous_merge_run(tmp_path, saturation_report, run_id="run-1")
    other = saturation_report([{"recommended_action": "ADD_CANDIDATE", "parent_status": "NO_PARENT",
                                "incoming_ids": [9002]}], run_id="run-2", name="other.json")
    capsys.readouterr()

    assert main(["bulk-decide", "--config", str(config_path), "--merge-run", str(tmp_path / "merge"),
                 "--report", str(other), "--create-below", "0.5", "--output", str(tmp_path / "d.json")]) == 1
    assert "use the report the producers read" in capsys.readouterr().err
    assert not (tmp_path / "d.json").exists()


def test_bulk_decide_will_not_overwrite_a_decisions_file_it_was_not_given(tmp_path, saturation_report, capsys):
    config_path, report, _ = _ambiguous_merge_run(tmp_path, saturation_report)
    decisions = tmp_path / "decisions.json"
    decisions.write_text("[]")

    assert main(["bulk-decide", "--config", str(config_path), "--merge-run", str(tmp_path / "merge"),
                 "--report", str(report), "--create-below", "0.5", "--output", str(decisions)]) == 1
    assert "pass it as --decisions" in capsys.readouterr().err
    assert decisions.read_text() == "[]"


def test_bulk_decide_refuses_a_merge_run_with_no_saturation_run_recorded(tmp_path, saturation_report, capsys):
    config_path, report, case_a_output = _ambiguous_merge_run(tmp_path, saturation_report)
    unstamped = tmp_path / "unstamped.json"
    unstamped.write_text(json.dumps([{key: value for key, value in record.items() if key != "saturation_run_id"}
                                     for record in json.loads(case_a_output.read_text())]))
    main(["merge-preview", "--config", str(config_path), "--input", str(unstamped),
          "--out-dir", str(tmp_path / "unstamped-merge")])
    capsys.readouterr()

    assert main(["bulk-decide", "--config", str(config_path), "--merge-run", str(tmp_path / "unstamped-merge"),
                 "--report", str(report), "--create-below", "0.5", "--output", str(tmp_path / "d.json")]) == 1
    assert "records no saturation run" in capsys.readouterr().err


def _integrated_round(tmp_path, saturation_report, classification_paths, metrics, **config_fields):
    """A round config scoring `original` with `metrics`, taken through case-a, merge-preview and integrate.
    Returns the config's path and the snapshot folder."""
    config_path = _write_stage_config(tmp_path)
    config = yaml.safe_load(config_path.read_text())
    config["datasets"][2:] = [{"name": kind, "type": kind, "paths": [str(path)]}
                              for kind, path in classification_paths.items()]
    config.update(metrics=metrics, **config_fields)
    config_path.write_text(yaml.safe_dump(config))
    report = saturation_report([
        {"recommended_action": "ADD_CANDIDATE", "parent_status": "HAS_PARENT", "incoming_ids": [9001]},
    ])
    main(["case-a", "--config", str(config_path), "--report", str(report), "--semantic-model", "",
          "--output", str(tmp_path / "case_a.json")])
    main(["merge-preview", "--config", str(config_path), "--input", str(tmp_path / "case_a.json"),
          "--out-dir", str(tmp_path / "merge")])
    assert main(["integrate", "--config", str(config_path), "--merge-run", str(tmp_path / "merge"),
                 "--out-dir", str(tmp_path / "enriched")]) == 0
    return config_path, tmp_path / "enriched"


def test_enriched_config_scores_the_snapshot_with_the_rounds_metrics(
    tmp_path, saturation_report, classification_paths, capsys, monkeypatch,
):
    _integrated_round(tmp_path, saturation_report, classification_paths, [
        {"id": "integrity", "params": {"dataset": "original", "fuzzy_threshold": 70}},
        {"id": "integrity", "params": {"dataset": "skillsfuture"}},  # scores another dataset: left out
        {"id": "breadth", "params": {"target": "original", "matching_method": "lexical"}},
        {"id": "saturation", "params": {"baseline": "original", "incoming": ["skillsfuture"]}},
    ])
    capsys.readouterr()

    enriched = yaml.safe_load((tmp_path / "enriched" / "enriched.yaml").read_text())
    assert [(m["id"], m["params"]) for m in enriched["metrics"]] == [
        ("depth", {"dataset": "enriched"}),
        ("integrity", {"dataset": "enriched", "fuzzy_threshold": 70}),
        ("breadth", {"target": "enriched", "matching_method": "lexical"}),
        ("saturation", {"baseline": "enriched", "incoming": ["skillsfuture"]}),
    ]
    assert [d["name"] for d in enriched["datasets"]] == ["enriched", "skillsfuture", "ssic", "ssoc"]

    # One run from another directory gives the whole scorecard.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert main(["run", "--config", str(tmp_path / "enriched" / "enriched.yaml"), "--no-save"]) == 0
    out = capsys.readouterr()
    for metric_id in ("integrity", "depth", "breadth", "saturation"):
        assert f"[OK] {metric_id}" in out.err + out.out


def test_a_pinned_round_scores_the_baseline_and_its_snapshot_comparably(
    tmp_path, saturation_report, classification_paths, capsys,
):
    config_path, snapshot = _integrated_round(tmp_path, saturation_report, classification_paths, [
        {"id": "integrity", "params": {"dataset": "original", "fuzzy_threshold": 85}},
        {"id": "depth", "params": {"dataset": "original"}},
        {"id": "breadth", "params": {"target": "original", "matching_method": "lexical"}},
        {"id": "saturation", "params": {"baseline": "original", "incoming": ["skillsfuture"]}},
    ], metric_definitions_version=METRIC_DEFINITIONS_VERSION)
    baseline, enriched = tmp_path / "baseline.json.gz", tmp_path / "enriched.json"
    assert main(["run", "--config", str(config_path), "--output", str(baseline), "--no-save"]) == 0
    assert main(["run", "--config", str(snapshot / "enriched.yaml"), "--output", str(enriched), "--no-save"]) == 0
    capsys.readouterr()

    assert main(["check-comparable", str(baseline), str(enriched)]) == 0
    out = capsys.readouterr().out
    assert [line for line in out.splitlines() if line.startswith("[")] == [
        "[OK] integrity", "[OK] depth", "[OK] breadth", "[OK] saturation",
    ]
    version = f"definitions v{METRIC_DEFINITIONS_VERSION}"
    assert f"(original, {version}), after" in out and f"(enriched, {version})" in out

    # Scoring the snapshot another way is flagged, with what changed.
    config = yaml.safe_load((snapshot / "enriched.yaml").read_text())
    next(entry for entry in config["metrics"] if entry["id"] == "breadth")["params"]["threshold"] = 60
    (snapshot / "lenient.yaml").write_text(yaml.safe_dump(config))
    main(["run", "--config", str(snapshot / "lenient.yaml"), "--output", str(tmp_path / "lenient.json"), "--no-save"])
    capsys.readouterr()

    assert main(["check-comparable", str(baseline), str(tmp_path / "lenient.json")]) == 1
    out = capsys.readouterr().out
    assert "[FLAGGED] breadth: scored with different settings: threshold 75.0 -> 60" in out
    assert "NOT comparable" in out


def test_check_comparable_reads_runs_from_history_by_id(tmp_path, isolated_db, capsys):
    from taxonomy_pipeline import db

    config_path = _write_config(tmp_path, [{"id": "integrity", "params": {"dataset": "original"}}])
    main(["run", "--config", str(config_path)])
    main(["run", "--config", str(config_path)])
    newer, older = (record.run_id for record in db.list_runs())
    capsys.readouterr()

    assert main(["check-comparable", older, newer]) == 0
    assert "[OK] integrity" in capsys.readouterr().out


def test_check_comparable_fails_cleanly_for_an_unknown_report(tmp_path, isolated_db, capsys):
    assert main(["check-comparable", "no-such-run", str(tmp_path / "missing.json")]) == 1
    assert "neither a report file nor a run ID" in capsys.readouterr().err
