from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from taxonomy_pipeline.enrichment.lineage import file_fingerprint, read_rounds

BASELINE = {"tags.sql": "INSERT INTO tags VALUES (1,'Python');\n"}


def test_fingerprint_ignores_file_order_and_location(tmp_path: Path) -> None:
    first, second = tmp_path / "a.sql", tmp_path / "b.sql"
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")
    copy = tmp_path / "elsewhere" / "a.sql"
    copy.parent.mkdir()
    copy.write_text("one", encoding="utf-8")

    assert file_fingerprint([first, second]) == file_fingerprint([second, copy])


def test_fingerprint_is_none_when_a_file_is_missing(tmp_path: Path) -> None:
    assert file_fingerprint([tmp_path / "missing.sql"]) is None


def test_fingerprint_changes_when_a_file_is_rewritten(tmp_path: Path) -> None:
    path = tmp_path / "tags.sql"
    path.write_text("before", encoding="utf-8")
    before = file_fingerprint([path])
    path.write_text("after!", encoding="utf-8")

    assert file_fingerprint([path]) != before


def test_reads_a_round_with_its_source_counts_and_both_fingerprints(
    tmp_path: Path, integration_round: Callable[..., Path],
) -> None:
    integration_round("round-1", BASELINE, "INSERT INTO tags VALUES (2,'Rust');\n",
                      counts={"tags": 1, "specialities": 3})

    [round_] = read_rounds(tmp_path / "integrations", tmp_path / "merge_runs")

    assert round_.name == "round-1" and round_.baseline_dataset == "original"
    assert round_.sources == ["skillsfuture"]
    assert round_.added == {"tags": 1, "specialities": 3}
    assert round_.created_at == datetime.fromisoformat("2026-10-15T10:00:00+00:00")
    assert round_.baseline != round_.snapshot and len(round_.snapshot) == 2


def test_a_later_rounds_baseline_matches_the_earlier_snapshot(
    tmp_path: Path, integration_round: Callable[..., Path],
) -> None:
    first = integration_round("round-1", BASELINE, "INSERT INTO tags VALUES (2,'Rust');\n")
    integration_round(
        "round-2", {"tags.sql": BASELINE["tags.sql"], "additions.sql": (first / "additions.sql").read_text()},
        "INSERT INTO tags VALUES (3,'Go');\n", source="onet", dataset="round_baseline",
    )

    rounds = {item.name: item for item in read_rounds(tmp_path / "integrations", tmp_path / "merge_runs")}

    assert rounds["round-2"].baseline == rounds["round-1"].snapshot


def _write_resolution(tmp_path: Path, tags: list[dict[str, Any]]) -> None:
    resolution = tmp_path / "merge_runs" / "round-1-reviewed" / "resolution.json"
    resolution.write_text(json.dumps({"tags": tags}), encoding="utf-8")


def _tag(existing: bool, *sources: str) -> dict[str, Any]:
    return {"existing": existing, "members": [{"source_dataset": source, "tag_id": "1"} for source in sources]}


def test_sources_are_listed_most_added_tags_first(tmp_path: Path, integration_round: Callable[..., Path]) -> None:
    integration_round("round-1", BASELINE, "x", source=None)
    _write_resolution(tmp_path, [_tag(False, "onet"), _tag(False, "skillsfuture"), _tag(False, "skillsfuture")])

    [round_] = read_rounds(tmp_path / "integrations", tmp_path / "merge_runs")

    assert round_.sources == ["skillsfuture", "onet"]


def test_sources_count_only_tags_the_round_added(tmp_path: Path, integration_round: Callable[..., Path]) -> None:
    # A mixed-source merge where onet's tags all matched the baseline: reused, so onet added nothing.
    integration_round("round-1", BASELINE, "x", source=None)
    _write_resolution(tmp_path, [_tag(False, "skillsfuture"), _tag(True, "onet"), _tag(True, "onet", "skillsfuture")])

    [round_] = read_rounds(tmp_path / "integrations", tmp_path / "merge_runs")

    assert round_.sources == ["skillsfuture"]


def test_a_tag_merged_from_two_sources_counts_for_both(tmp_path: Path, integration_round: Callable[..., Path]) -> None:
    integration_round("round-1", BASELINE, "x", source=None)
    _write_resolution(tmp_path, [_tag(False, "onet", "skillsfuture"), _tag(False, "skillsfuture")])

    [round_] = read_rounds(tmp_path / "integrations", tmp_path / "merge_runs")

    assert round_.sources == ["skillsfuture", "onet"]


def test_a_missing_merge_run_leaves_sources_empty(tmp_path: Path, integration_round: Callable[..., Path]) -> None:
    integration_round("round-1", BASELINE, "x")

    [round_] = read_rounds(tmp_path / "integrations", tmp_path / "elsewhere")

    assert round_.sources == []


def test_skips_rounds_without_a_readable_manifest_or_files(
    tmp_path: Path, integration_round: Callable[..., Path],
) -> None:
    integration_round("good", BASELINE, "x")
    broken = integration_round("broken", BASELINE, "x")
    (broken / "additions.sql").unlink()
    (tmp_path / "integrations" / "bad-json").mkdir()
    (tmp_path / "integrations" / "bad-json" / "manifest.json").write_text("{", encoding="utf-8")
    (tmp_path / "integrations" / "no-manifest").mkdir()

    rounds = read_rounds(tmp_path / "integrations", tmp_path / "merge_runs")

    assert [item.name for item in rounds] == ["good"]


def test_no_integrations_directory_means_no_rounds(tmp_path: Path) -> None:
    assert read_rounds(tmp_path / "integrations", tmp_path / "merge_runs") == []
