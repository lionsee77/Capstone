from __future__ import annotations

import json
from datetime import datetime

from taxonomy_pipeline.enrichment.lineage import file_fingerprint, read_rounds

BASELINE = {"tags.sql": "INSERT INTO tags VALUES (1,'Python');\n"}


def test_fingerprint_ignores_file_order_and_location(tmp_path):
    first, second = tmp_path / "a.sql", tmp_path / "b.sql"
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")
    copy = tmp_path / "elsewhere" / "a.sql"
    copy.parent.mkdir()
    copy.write_text("one", encoding="utf-8")

    assert file_fingerprint([first, second]) == file_fingerprint([second, copy])


def test_fingerprint_is_none_when_a_file_is_missing(tmp_path):
    assert file_fingerprint([tmp_path / "missing.sql"]) is None


def test_fingerprint_changes_when_a_file_is_rewritten(tmp_path):
    path = tmp_path / "tags.sql"
    path.write_text("before", encoding="utf-8")
    before = file_fingerprint([path])
    path.write_text("after!", encoding="utf-8")

    assert file_fingerprint([path]) != before


def test_reads_a_round_with_its_source_counts_and_both_fingerprints(tmp_path, integration_round):
    integration_round("round-1", BASELINE, "INSERT INTO tags VALUES (2,'Rust');\n",
                      counts={"tags": 1, "specialities": 3})

    [round_] = read_rounds(tmp_path / "integrations", tmp_path / "merge_runs")

    assert round_.name == "round-1" and round_.baseline_dataset == "original"
    assert round_.sources == ["skillsfuture"]
    assert round_.added == {"tags": 1, "specialities": 3}
    assert round_.created_at == datetime.fromisoformat("2026-10-15T10:00:00+00:00")
    assert round_.baseline != round_.snapshot and len(round_.snapshot) == 2


def test_a_later_rounds_baseline_matches_the_earlier_snapshot(tmp_path, integration_round):
    first = integration_round("round-1", BASELINE, "INSERT INTO tags VALUES (2,'Rust');\n")
    integration_round(
        "round-2", {"tags.sql": BASELINE["tags.sql"], "additions.sql": (first / "additions.sql").read_text()},
        "INSERT INTO tags VALUES (3,'Go');\n", source="onet", dataset="round_baseline",
    )

    rounds = {item.name: item for item in read_rounds(tmp_path / "integrations", tmp_path / "merge_runs")}

    assert rounds["round-2"].baseline == rounds["round-1"].snapshot


def test_sources_are_listed_most_records_first(tmp_path, integration_round):
    integration_round("round-1", BASELINE, "x", source=None)
    validation = tmp_path / "merge_runs" / "round-1-reviewed" / "validation.json"
    records = [{"raw": {"source_dataset": name}} for name in ["onet", "skillsfuture", "skillsfuture"]]
    validation.write_text(json.dumps({"records": records}), encoding="utf-8")

    [round_] = read_rounds(tmp_path / "integrations", tmp_path / "merge_runs")

    assert round_.sources == ["skillsfuture", "onet"]


def test_a_missing_merge_run_leaves_sources_empty(tmp_path, integration_round):
    integration_round("round-1", BASELINE, "x")

    [round_] = read_rounds(tmp_path / "integrations", tmp_path / "elsewhere")

    assert round_.sources == []


def test_skips_rounds_without_a_readable_manifest_or_files(tmp_path, integration_round):
    integration_round("good", BASELINE, "x")
    broken = integration_round("broken", BASELINE, "x")
    (broken / "additions.sql").unlink()
    (tmp_path / "integrations" / "bad-json").mkdir()
    (tmp_path / "integrations" / "bad-json" / "manifest.json").write_text("{", encoding="utf-8")
    (tmp_path / "integrations" / "no-manifest").mkdir()

    rounds = read_rounds(tmp_path / "integrations", tmp_path / "merge_runs")

    assert [item.name for item in rounds] == ["good"]


def test_no_integrations_directory_means_no_rounds(tmp_path):
    assert read_rounds(tmp_path / "integrations", tmp_path / "merge_runs") == []
