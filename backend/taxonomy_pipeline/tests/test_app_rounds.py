"""The round use cases: a source goes in, tracked stages run the real pipeline, and a snapshot comes out."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from taxonomy_pipeline.app import rounds
from taxonomy_pipeline.config import load_config
from taxonomy_pipeline.models import RoundSettings
from taxonomy_pipeline.taxonomy.flat_import import SourceMapping
from tests.conftest import FIXTURES

CSV = (
    "Sector,Role,Skill,Desc\n"
    "Marketing,Brand Strategy,Underwater Welding,Joining metal below water\n"
    "Marketing,Brand Strategy,Payroll Software,\n"
    ",,Orphan Skill,\n"
)


@pytest.fixture(autouse=True)
def no_llm_or_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rounds in tests use spelling matches only, with the server's LLM switch off."""
    monkeypatch.setattr(rounds, "SEMANTIC_MODEL", "")
    monkeypatch.delenv("TAXONOMY_ALLOW_LLM", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setattr(rounds, "_active", set())  # a test that stops mid-round mustn't block the next one


@pytest.fixture
def roots(tmp_path: Path) -> rounds.RoundRoots:
    """Real folders with one snapshot to build on, and a template that needs no reference data or model."""
    snapshot = tmp_path / "integrations" / "base"
    (snapshot / "baseline").mkdir(parents=True)
    # The real taxonomy's service names are the only ones the classification contract accepts.
    baseline = (FIXTURES / "mini_original.sql").read_text("utf-8").replace("Professional Services", "Industry")
    (snapshot / "baseline" / "original.sql").write_text(baseline, "utf-8")
    (snapshot / "additions.sql").write_text("INSERT INTO `tags` (`id`, `name`) VALUES\n(200, 'audit');\n", "utf-8")
    (snapshot / "enriched.yaml").write_text(yaml.safe_dump({
        "datasets": [{"name": "enriched", "type": "sql_dump", "paths": ["baseline/original.sql", "additions.sql"]}],
        "metrics": [{"id": "integrity", "params": {"dataset": "enriched"}}],
    }), "utf-8")
    (snapshot / "manifest.json").write_text(json.dumps({
        "created_at": "2026-10-01T00:00:00+00:00",
        "baseline": {"dataset": "original", "files": ["baseline/original.sql"]},
    }), "utf-8")
    template = tmp_path / "template.yaml"
    template.write_text(yaml.safe_dump({
        "datasets": [{"name": "original", "paths": ["o.sql"]}, {"name": "incoming", "paths": ["i.sql"]}],
        "metrics": [
            {"id": "integrity", "params": {"dataset": "original"}},
            {"id": "saturation", "params": {
                "baseline": "original", "incoming": ["incoming"], "threshold": 90,
                "judge": {"model": "m", "cache": "judge_cache.json"},
            }},
        ],
    }), "utf-8")
    return rounds.RoundRoots(
        rounds=tmp_path / "rounds", integrations=tmp_path / "integrations", merge_runs=tmp_path / "merge_runs",
        template=template,
    )


def _mapping(**overrides: object) -> SourceMapping:
    return SourceMapping.model_validate({
        "name": "demo", "display_name": "Demo", "source": "flat.csv", "service_id": 1,
        "columns": {"category": "Sector", "speciality": "Role", "tag": "Skill", "tag_description": "Desc"},
        **overrides,
    })


@pytest.fixture
def configured(roots: rounds.RoundRoots, isolated_db: Path) -> str:
    """A round with its source uploaded and imported, ready to run."""
    rounds.create_round(roots, "demo-round", "base")
    rounds.add_file(roots, "demo-round", "flat.csv", CSV.encode())
    rounds.configure_round(roots, "demo-round", _mapping(), RoundSettings())
    return "demo-round"


def _run(roots: rounds.RoundRoots, round_id: str) -> rounds.RoundState:
    _, stages = rounds.start_round(roots, round_id)
    rounds.run_stages(roots, round_id, stages)
    return rounds.get_round(roots, round_id)


def _statuses(state: rounds.RoundState) -> dict[str, str]:
    return {stage.id: stage.status for stage in state.stages}


def test_a_new_round_starts_in_setup_with_every_stage_pending(roots: rounds.RoundRoots) -> None:
    state = rounds.create_round(roots, "r1", "base")

    assert state.status == "setup"
    assert set(_statuses(state).values()) == {"pending"}
    assert rounds.get_round(roots, "r1").baseline_snapshot == "base"


@pytest.mark.parametrize("name", ["", "Has Space", "UPPER", "../escape", "a/b", "-leading"])
def test_a_round_name_that_cannot_name_a_folder_is_refused(roots: rounds.RoundRoots, name: str) -> None:
    with pytest.raises(ValueError, match="round name"):
        rounds.create_round(roots, name, "base")


def test_a_name_that_is_a_route_under_rounds_is_refused(roots: rounds.RoundRoots) -> None:
    with pytest.raises(ValueError, match="route"):
        rounds.create_round(roots, "options", "base")


def test_a_round_must_build_on_a_real_snapshot(roots: rounds.RoundRoots) -> None:
    for snapshot in ("nope", "../base"):
        with pytest.raises(ValueError, match="no integrated snapshot"):
            rounds.create_round(roots, "r1", snapshot)


@pytest.mark.parametrize("parent", ["rounds", "merge_runs", "integrations"])
def test_a_name_already_used_by_any_output_is_refused(roots: rounds.RoundRoots, parent: str) -> None:
    (getattr(roots, "merge_runs" if parent == "merge_runs" else parent) / "taken").mkdir(parents=True)

    with pytest.raises(rounds.RoundConflict, match="already used"):
        rounds.create_round(roots, "taken", "base")


def test_options_offer_the_snapshots_services_and_template_defaults(roots: rounds.RoundRoots) -> None:
    options = rounds.round_options(roots)

    assert options.snapshots == ["base"]
    assert [(service.id, service.name) for service in options.services] == [(1, "Industry")]
    assert options.saturation_defaults == {"threshold": 90}
    assert not options.llm_allowed


def test_an_uploaded_file_lists_its_columns_rows_and_samples(roots: rounds.RoundRoots) -> None:
    rounds.create_round(roots, "r1", "base")

    uploaded = rounds.add_file(roots, "r1", "flat.csv", CSV.encode())

    assert uploaded.columns == ["Sector", "Role", "Skill", "Desc"]
    assert uploaded.row_count == 3
    assert uploaded.samples["Sector"] == ["Marketing"]
    assert [item.name for item in rounds.list_files(roots, "r1")] == ["flat.csv"]


@pytest.mark.parametrize("name", ["../evil.csv", "a/b.csv", ".hidden.csv", "notes.txt", "mapping.yaml", ""])
def test_a_file_name_that_is_not_a_plain_table_is_refused(roots: rounds.RoundRoots, name: str) -> None:
    rounds.create_round(roots, "r1", "base")

    with pytest.raises(ValueError):
        rounds.add_file(roots, "r1", name, b"a,b\n")
    assert not (roots.rounds.parent / "evil.csv").exists()


def test_an_oversized_upload_is_refused(roots: rounds.RoundRoots, monkeypatch: pytest.MonkeyPatch) -> None:
    rounds.create_round(roots, "r1", "base")
    monkeypatch.setattr(rounds, "MAX_UPLOAD_BYTES", 4)

    with pytest.raises(ValueError, match="larger"):
        rounds.add_file(roots, "r1", "flat.csv", b"12345")


def test_an_unreadable_table_reports_why_instead_of_failing_the_listing(roots: rounds.RoundRoots) -> None:
    rounds.create_round(roots, "r1", "base")

    uploaded = rounds.add_file(roots, "r1", "bad.xlsx", b"not a workbook")

    assert uploaded.error
    assert [item.name for item in rounds.list_files(roots, "r1")] == ["bad.xlsx"]


def test_a_file_can_be_removed_and_an_unknown_one_is_not_found(roots: rounds.RoundRoots) -> None:
    rounds.create_round(roots, "r1", "base")
    rounds.add_file(roots, "r1", "flat.csv", CSV.encode())

    rounds.remove_file(roots, "r1", "flat.csv")

    assert rounds.list_files(roots, "r1") == []
    with pytest.raises(rounds.RoundNotFound):
        rounds.remove_file(roots, "r1", "flat.csv")


def test_configuring_imports_the_source_and_writes_a_config_that_loads(
    roots: rounds.RoundRoots, configured: str,
) -> None:
    state = rounds.get_round(roots, configured)

    assert state.status == "ready"
    assert state.source_name == "demo"
    assert state.import_counts["tags"] == 3
    assert any("without complete parent paths" in warning for warning in state.warnings)
    config = load_config(roots.rounds / configured / "config.yaml")
    assert {dataset.name for dataset in config.datasets} == {"round_baseline", "demo"}
    assert all(Path(path).is_file() for dataset in config.datasets for path in dataset.paths)
    assert "round_baseline" in rounds.round_config_text(roots, configured)


def test_a_failed_mapping_keeps_the_last_good_import(roots: rounds.RoundRoots, configured: str) -> None:
    bad = _mapping(columns={"category": "Sector", "speciality": "Role", "tag": "Nope"})

    with pytest.raises(ValueError, match="Nope"):
        rounds.configure_round(roots, configured, bad, RoundSettings())

    assert rounds.get_round(roots, configured).status == "ready"
    assert "Skill" in (roots.rounds / configured / "source" / "mapping.yaml").read_text("utf-8")
    load_config(roots.rounds / configured / "config.yaml")


def test_a_mapping_can_only_name_files_in_this_round(roots: rounds.RoundRoots, configured: str, tmp_path: Path) -> None:
    (tmp_path / "secret.csv").write_text("Skill\nx\n", "utf-8")

    for source in ("../../../secret.csv", str(tmp_path / "secret.csv"), "missing.csv"):
        with pytest.raises(ValueError, match="not uploaded"):
            rounds.configure_round(roots, configured, _mapping(source=source), RoundSettings())


def test_replacing_the_imported_table_requires_another_import(roots: rounds.RoundRoots, configured: str) -> None:
    _run(roots, configured)
    corrected = CSV.replace("Payroll Software", "Payroll Platform").encode()

    rounds.add_file(roots, configured, "flat.csv", corrected)

    state = rounds.get_round(roots, configured)
    assert state.status == "setup"
    assert set(_statuses(state).values()) == {"pending"}
    assert state.score_run_id is None
    assert state.import_counts == {}
    assert "flat.csv changed" in state.warnings[0]
    assert not (roots.rounds / configured / "report.json").exists()
    assert not (roots.merge_runs / configured).exists()
    with pytest.raises(rounds.RoundConflict, match="mapping"):
        rounds.start_round(roots, configured)


def test_the_replacement_is_what_the_next_import_and_run_use(roots: rounds.RoundRoots, configured: str) -> None:
    rounds.add_file(roots, configured, "flat.csv", CSV.replace("Payroll Software", "Payroll Platform").encode())

    state = rounds.configure_round(roots, configured, _mapping(), RoundSettings())

    assert state.status == "ready"
    assert not any("changed" in warning for warning in state.warnings)
    dump = (roots.rounds / configured / "demo_taxonomy.sql").read_text("utf-8")
    assert "Payroll Platform" in dump and "Payroll Software" not in dump
    assert _statuses(_run(roots, configured))["merge"] == "done"


def test_uploading_the_same_bytes_again_keeps_the_rounds_progress(roots: rounds.RoundRoots, configured: str) -> None:
    run_id = _run(roots, configured).score_run_id

    rounds.add_file(roots, configured, "flat.csv", CSV.encode())

    state = rounds.get_round(roots, configured)
    assert state.score_run_id == run_id
    assert _statuses(state)["merge"] == "done"


def test_a_file_the_mapping_does_not_use_changes_nothing(roots: rounds.RoundRoots, configured: str) -> None:
    rounds.add_file(roots, configured, "notes.csv", b"a,b\n1,2\n")
    rounds.add_file(roots, configured, "notes.csv", b"a,b\n3,4\n")
    rounds.remove_file(roots, configured, "notes.csv")

    assert rounds.get_round(roots, configured).status == "ready"


def test_removing_the_imported_table_requires_another_import(roots: rounds.RoundRoots, configured: str) -> None:
    rounds.remove_file(roots, configured, "flat.csv")

    assert rounds.get_round(roots, configured).status == "setup"


def test_changing_a_labels_file_the_mapping_reads_requires_another_import(
    roots: rounds.RoundRoots, isolated_db: Path,
) -> None:
    rounds.create_round(roots, "coded", "base")
    rounds.add_file(roots, "coded", "flat.csv", b"Code,Role,Skill\nMK-1,Brand Strategy,Underwater Welding\n")
    rounds.add_file(roots, "coded", "labels.csv", b"code,label\nMK,Marketing\n")
    mapping = _mapping(columns={
        "category": {"column": "Code", "prefix": 2, "labels": "labels.csv"}, "speciality": "Role", "tag": "Skill",
    })
    assert rounds.configure_round(roots, "coded", mapping, RoundSettings()).status == "ready"

    rounds.add_file(roots, "coded", "labels.csv", b"code,label\nMK,Marketing and Sales\n")

    assert rounds.get_round(roots, "coded").status == "setup"


def test_the_judge_is_stripped_unless_the_round_turns_it_on(roots: rounds.RoundRoots, configured: str) -> None:
    config = yaml.safe_load((roots.rounds / configured / "config.yaml").read_text("utf-8"))
    params = next(metric["params"] for metric in config["metrics"] if metric["id"] == "saturation")

    assert "judge" not in params


def test_thresholds_from_the_form_replace_the_templates(roots: rounds.RoundRoots, configured: str) -> None:
    rounds.configure_round(roots, configured, _mapping(), RoundSettings(threshold=75))

    config = yaml.safe_load((roots.rounds / configured / "config.yaml").read_text("utf-8"))
    params = next(metric["params"] for metric in config["metrics"] if metric["id"] == "saturation")
    assert params["threshold"] == 75


@pytest.mark.parametrize("settings", [{"judge": True}, {"case_b": True}])
def test_llm_steps_are_refused_unless_the_server_allows_them(
    roots: rounds.RoundRoots, configured: str, settings: dict[str, bool],
) -> None:
    with pytest.raises(ValueError, match="TAXONOMY_ALLOW_LLM"):
        rounds.configure_round(roots, configured, _mapping(), RoundSettings(**settings))


def test_llm_steps_also_need_a_real_key(
    roots: rounds.RoundRoots, configured: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAXONOMY_ALLOW_LLM", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "your-openai-api-key")

    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        rounds.configure_round(roots, configured, _mapping(), RoundSettings(judge=True))


def test_an_allowed_judge_caches_beside_rounds_whatever_the_working_directory(
    roots: rounds.RoundRoots, configured: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAXONOMY_ALLOW_LLM", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "real-looking-key")

    rounds.configure_round(roots, configured, _mapping(), RoundSettings(judge=True))

    config = yaml.safe_load((roots.rounds / configured / "config.yaml").read_text("utf-8"))
    judge = next(metric["params"]["judge"] for metric in config["metrics"] if metric["id"] == "saturation")
    assert judge["cache"] == str(roots.rounds.parent.resolve() / "judge_cache_demo.json")


def test_a_round_cannot_run_before_it_has_a_mapping(roots: rounds.RoundRoots) -> None:
    rounds.create_round(roots, "r1", "base")

    with pytest.raises(rounds.RoundConflict, match="mapping"):
        rounds.start_round(roots, "r1")


def test_running_scores_and_places_the_tags_then_stops_for_review(roots: rounds.RoundRoots, configured: str) -> None:
    state = _run(roots, configured)

    assert _statuses(state) == {
        "import": "done", "score": "done", "case_a": "done", "case_b": "skipped", "merge": "done",
        "integrate": "pending", "score_enriched": "pending",
    }
    assert state.score_run_id
    assert state.merge_counts["added_tags"] + state.pending_sources > 0
    assert (roots.merge_runs / configured / "resolution.json").is_file()
    assert state.status in {"review", "ready_to_integrate"}


def test_new_parentless_tags_are_reported_when_llm_classification_is_off(
    roots: rounds.RoundRoots, configured: str,
) -> None:
    state = _run(roots, configured)

    case_b = next(stage for stage in state.stages if stage.id == "case_b")
    assert case_b.status == "skipped"
    assert "Left out 1 new tag" in case_b.summary
    assert case_b.summary in state.warnings


def test_a_failed_stage_records_why_and_stops_the_round(
    roots: rounds.RoundRoots, configured: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("no such model")

    monkeypatch.setattr(rounds.use_cases, "case_a", fail)

    state = _run(roots, configured)

    assert state.status == "failed"
    statuses = _statuses(state)
    assert (statuses["score"], statuses["case_a"], statuses["merge"]) == ("done", "failed", "pending")
    assert "no such model" in next(stage for stage in state.stages if stage.id == "case_a").error


def test_a_retry_resumes_at_the_failed_stage_without_repeating_finished_ones(
    roots: rounds.RoundRoots, configured: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = rounds.use_cases.case_a

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("temporary")

    monkeypatch.setattr(rounds.use_cases, "case_a", fail)
    first_run = _run(roots, configured).score_run_id
    monkeypatch.setattr(rounds.use_cases, "case_a", real)

    state, stages = rounds.start_round(roots, configured)

    assert stages == ["case_a", "case_b", "merge"]
    rounds.run_stages(roots, configured, stages)
    state = rounds.get_round(roots, configured)
    assert state.score_run_id == first_run
    assert _statuses(state)["merge"] == "done"


def test_a_stage_left_running_by_a_restart_reads_back_as_failed_and_can_resume(
    roots: rounds.RoundRoots, configured: str,
) -> None:
    path = roots.rounds / configured / "round.json"
    saved = json.loads(path.read_text("utf-8"))
    next(stage for stage in saved["stages"] if stage["id"] == "score")["status"] = "running"
    path.write_text(json.dumps(saved), "utf-8")

    state = rounds.get_round(roots, configured)

    assert state.status == "failed"
    assert "Interrupted" in next(stage for stage in state.stages if stage.id == "score").error
    assert _statuses(_run(roots, configured))["merge"] == "done"


def test_a_running_round_cannot_be_started_or_changed_again(roots: rounds.RoundRoots, configured: str) -> None:
    _, stages = rounds.start_round(roots, configured)

    with pytest.raises(rounds.RoundConflict, match="already running"):
        rounds.start_round(roots, configured)
    with pytest.raises(rounds.RoundConflict, match="running"):
        rounds.add_file(roots, configured, "other.csv", b"a\n")
    with pytest.raises(rounds.RoundConflict):
        rounds.delete_round(roots, configured)

    rounds.run_stages(roots, configured, stages)
    assert rounds.get_round(roots, configured).status != "running"


def test_nothing_can_start_or_remove_a_round_while_it_is_being_reconfigured(
    roots: rounds.RoundRoots, configured: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import = rounds.import_source
    refused = []

    def during_import(*args: object, **kwargs: object) -> object:
        for attempt in (rounds.start_round, rounds.delete_round):
            with pytest.raises(rounds.RoundConflict):
                attempt(roots, configured)
            refused.append(attempt.__name__)
        return real_import(*args, **kwargs)

    monkeypatch.setattr(rounds, "import_source", during_import)

    state = rounds.configure_round(roots, configured, _mapping(), RoundSettings())

    assert refused == ["start_round", "delete_round"]
    assert state.status == "ready"
    assert _statuses(_run(roots, configured))["merge"] == "done"


def test_a_failed_reconfigure_releases_the_round(roots: rounds.RoundRoots, configured: str) -> None:
    with pytest.raises(ValueError):
        rounds.configure_round(roots, configured, _mapping(source="missing.csv"), RoundSettings())

    assert _statuses(_run(roots, configured))["merge"] == "done"


def test_a_stage_interrupted_by_a_restart_does_not_block_later_edits(roots: rounds.RoundRoots, configured: str) -> None:
    path = roots.rounds / configured / "round.json"
    saved = json.loads(path.read_text("utf-8"))
    next(stage for stage in saved["stages"] if stage["id"] == "score")["status"] = "running"
    path.write_text(json.dumps(saved), "utf-8")

    assert rounds.add_file(roots, configured, "other.csv", b"a,b\n1,2\n").columns == ["a", "b"]


def test_two_rounds_of_one_source_cannot_use_its_judge_cache_at_once(
    roots: rounds.RoundRoots, configured: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAXONOMY_ALLOW_LLM", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "real-looking-key")
    rounds.create_round(roots, "second", "base")
    rounds.add_file(roots, "second", "flat.csv", CSV.encode())
    judged = RoundSettings(judge=True)
    rounds.configure_round(roots, configured, _mapping(), judged)
    rounds.configure_round(roots, "second", _mapping(), judged)
    rounds.start_round(roots, configured)

    with pytest.raises(rounds.RoundConflict, match="judge cache"):
        rounds.start_round(roots, "second")


def test_rounds_of_one_source_can_run_together_without_the_judge(roots: rounds.RoundRoots, configured: str) -> None:
    rounds.create_round(roots, "second", "base")
    rounds.add_file(roots, "second", "flat.csv", CSV.encode())
    rounds.configure_round(roots, "second", _mapping(), RoundSettings())
    rounds.start_round(roots, configured)

    state, _ = rounds.start_round(roots, "second")

    assert state.status == "running"


def test_a_finished_round_has_nothing_left_to_start(roots: rounds.RoundRoots, configured: str) -> None:
    _run(roots, configured)

    with pytest.raises(rounds.RoundConflict, match="already run"):
        rounds.start_round(roots, configured)


def test_reconfiguring_discards_what_the_old_mapping_produced(roots: rounds.RoundRoots, configured: str) -> None:
    _run(roots, configured)

    state = rounds.configure_round(roots, configured, _mapping(), RoundSettings())

    assert state.status == "ready"
    assert state.score_run_id is None
    assert not (roots.rounds / configured / "report.json").exists()
    assert not (roots.merge_runs / configured).exists()


def test_deleting_removes_the_round_and_its_outputs(roots: rounds.RoundRoots, configured: str) -> None:
    _run(roots, configured)

    rounds.delete_round(roots, configured)

    assert not (roots.rounds / configured).exists()
    assert not (roots.merge_runs / configured).exists()
    assert rounds.list_rounds(roots) == []


def test_only_dashboard_rounds_can_be_deleted(roots: rounds.RoundRoots) -> None:
    with pytest.raises(rounds.RoundNotFound):
        rounds.delete_round(roots, "base")
    assert (roots.integrations / "base").exists()


def test_a_round_another_is_built_on_cannot_be_deleted(roots: rounds.RoundRoots) -> None:
    rounds.create_round(roots, "first", "base")
    snapshot = roots.integrations / "first"
    shutil.copytree(roots.integrations / "base", snapshot)
    rounds.create_round(roots, "second", "first")

    with pytest.raises(rounds.RoundConflict, match="second"):
        rounds.delete_round(roots, "first")
    assert snapshot.exists()


def test_an_unknown_round_is_not_found(roots: rounds.RoundRoots) -> None:
    for name in ("missing", "../base"):
        with pytest.raises(rounds.RoundNotFound):
            rounds.get_round(roots, name)


def test_listing_shows_newest_first_and_skips_unreadable_state(roots: rounds.RoundRoots) -> None:
    rounds.create_round(roots, "older", "base")
    rounds.create_round(roots, "newer", "base")
    (roots.rounds / "broken").mkdir()
    (roots.rounds / "broken" / "round.json").write_text("{", "utf-8")

    assert [state.round_id for state in rounds.list_rounds(roots)] == ["newer", "older"]
