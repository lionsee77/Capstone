"""Round use cases: run a whole enrichment round as tracked stages, the way the dashboard does.

A round is what the README's "Next round" section does by hand with CLI commands:
import a source, score it against the last snapshot, place its new tags, build a merge
preview, wait for a person, integrate, score the result. This module adds no pipeline
logic. Each stage calls the use case its CLI command calls, and what this adds is the
sequence: saved state, background execution, and resuming after a failure.

Layout. A round's inputs and intermediates live in `rounds/<round_id>/`. Its outputs go
where the CLI puts them, under the same name (`merge_runs/<round_id>`,
`integrations/<round_id>`), so `/lineage`, `/proposals`, `/lookup` and the Docker loader
see a dashboard round exactly like a hand-run one.

State is one JSON file, `round.json`, rewritten atomically after each change. Which
rounds are executing is kept in memory, so a stage still marked running after a restart
is known to have been interrupted and reads back as failed, ready to resume.

LLM steps spend money, so they are off unless the server opts in (`llm_status`). The
round config is built here rather than accepted from a caller, which is why `POST /runs`
can keep refusing judge settings: a caller picks a switch, never a cache path or model.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .. import env
from ..config import load_config
from ..enrichment.lineage import file_fingerprint, read_rounds
from ..enrichment.routing import ROUTED_ACTIONS, load_saturation
from ..llm import PLACEHOLDER_KEYS
from ..models import (
    RoundFile,
    RoundOptions,
    RoundServiceOption,
    RoundSettings,
    RoundStage,
    RoundStageId,
    RoundState,
    utcnow,
)
from ..taxonomy.flat_import import SourceMapping, inspect_table, sheet_names
from . import enrichment as use_cases
from .scoring import load_taxonomies, run_config, write_report
from .sources import DEFAULT_TEMPLATE, ROUND_BASELINE, import_source

ROUND_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
RESERVED_ROUND_IDS = {"options"}
UPLOAD_SUFFIXES = {".csv", ".tsv", ".xlsx", ".xlsm"}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_FILES = 20
# The model the CLI's classification commands default to; empty means lexical matching only.
SEMANTIC_MODEL = "all-MiniLM-L6-v2"
# Saturation settings the round form may override; everything else stays the template's.
SATURATION_SETTINGS = (
    "threshold", "semantic_threshold", "add_candidate_lexical_threshold", "add_candidate_semantic_threshold",
)
PIPELINE_STAGES: tuple[RoundStageId, ...] = ("score", "case_a", "case_b", "merge")
FINISHED = {"done", "skipped"}
SOURCE_DIR = "source"


class RoundNotFound(Exception):
    """No round has this ID."""


class RoundConflict(Exception):
    """The round exists, but its current state doesn't allow this; the message says why."""


@dataclass(frozen=True)
class RoundRoots:
    """Where rounds and their outputs live. The API and tests choose these; a request never does."""

    rounds: Path
    integrations: Path
    merge_runs: Path
    template: Path = DEFAULT_TEMPLATE

    def directory(self, round_id: str) -> Path:
        if not ROUND_ID.fullmatch(round_id):
            raise RoundNotFound(f"no round named {round_id!r}")
        return self.rounds / round_id


_lock = threading.Lock()
_active: set[str] = set()


def llm_status() -> tuple[bool, bool]:
    """Whether this server allows LLM steps, and whether it has a real key to run them with."""
    env.load_env()
    allowed = os.getenv("TAXONOMY_ALLOW_LLM", "").strip().lower() == "true"
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    return allowed, bool(key) and key not in PLACEHOLDER_KEYS


def round_options(roots: RoundRoots) -> RoundOptions:
    """What the new-round form offers: snapshots to build on, services, the switches and their defaults."""
    rounds = sorted(
        read_rounds(roots.integrations, roots.merge_runs),
        key=lambda item: (item.created_at.isoformat() if item.created_at else "", item.name), reverse=True,
    )
    snapshots = [item.name for item in rounds]
    services = []
    if snapshots:
        config_path = roots.integrations / snapshots[0] / "enriched.yaml"
        name = load_config(config_path).datasets[0].name
        dataset = load_taxonomies(config_path, name)[name]
        services = [RoundServiceOption(id=row["id"], name=str(row["name"])) for row in dataset.services]
    template = yaml.safe_load(roots.template.read_text(encoding="utf-8"))
    params = next(metric.get("params", {}) for metric in template["metrics"] if metric.get("id") == "saturation")
    allowed, key_configured = llm_status()
    return RoundOptions(
        snapshots=snapshots, services=services, llm_allowed=allowed, llm_key_configured=key_configured,
        saturation_defaults={name: params[name] for name in SATURATION_SETTINGS if name in params},
    )


def create_round(roots: RoundRoots, round_id: str, baseline_snapshot: str) -> RoundState:
    """An empty round building on `baseline_snapshot`. Its ID also names its merge run and snapshot."""
    if not ROUND_ID.fullmatch(round_id):
        raise ValueError("round name must be lowercase letters, digits, '-' or '_', starting with a letter or digit")
    if round_id in RESERVED_ROUND_IDS:
        raise ValueError(f"{round_id!r} can't name a round: it is a route under /rounds")
    if not (roots.integrations / baseline_snapshot / "enriched.yaml").is_file() or not ROUND_ID.fullmatch(
        baseline_snapshot,
    ):
        raise ValueError(f"no integrated snapshot named {baseline_snapshot!r}")
    for parent in (roots.rounds, roots.merge_runs, roots.integrations):
        if (parent / round_id).exists():
            raise RoundConflict(f"{round_id!r} is already used in {parent.name}/; choose another name")
    directory = roots.directory(round_id)
    (directory / SOURCE_DIR).mkdir(parents=True)
    state = RoundState(
        round_id=round_id, baseline_snapshot=baseline_snapshot, created_at=utcnow(),
        stages=[RoundStage(id=stage) for stage in ("import", *PIPELINE_STAGES, "integrate", "score_enriched")],
    )
    _save(roots, state)
    return state


def list_rounds(roots: RoundRoots) -> list[RoundState]:
    """Every round, newest first; one with unreadable state is skipped rather than hiding the rest."""
    if not roots.rounds.is_dir():
        return []
    states = []
    for directory in roots.rounds.iterdir():
        if (directory / "round.json").is_file():
            try:
                states.append(get_round(roots, directory.name))
            except (RoundNotFound, ValueError, OSError):
                continue
    return sorted(states, key=lambda state: state.created_at, reverse=True)


def get_round(roots: RoundRoots, round_id: str) -> RoundState:
    path = roots.directory(round_id) / "round.json"
    if not path.is_file():
        raise RoundNotFound(f"no round named {round_id!r}")
    state = RoundState.model_validate_json(path.read_text(encoding="utf-8"))
    with _lock:
        interrupted = round_id not in _active
    return _without_interrupted(state) if interrupted else state


def _without_interrupted(state: RoundState) -> RoundState:
    """A stage marked running that nothing in this process is running: the server restarted mid-stage."""
    for stage in state.stages:
        if stage.status == "running":
            stage.status, stage.error = "failed", "Interrupted when the server stopped; run the round again"
            stage.finished_at = utcnow()
    return state


@contextmanager
def _reserved(roots: RoundRoots, round_id: str) -> Iterator[RoundState]:
    """Hold the round for a change that takes a while, so a run or removal can't start partway through it."""
    with _lock:
        state = _claimable(roots, round_id)
        _require_editable(state)
        _active.add(round_id)
    try:
        yield state
    finally:
        with _lock:
            _active.discard(round_id)


def round_config_text(roots: RoundRoots, round_id: str) -> str:
    """The generated round config, for showing what a round will run."""
    get_round(roots, round_id)
    path = _config_path(roots, round_id)
    if not path.is_file():
        raise RoundConflict("this round has no config yet; save its mapping first")
    return path.read_text(encoding="utf-8")


def add_file(roots: RoundRoots, round_id: str, filename: str, content: bytes) -> RoundFile:
    """Save an uploaded source table or labels file into the round, flat, under its own name."""
    if not filename or filename != Path(filename).name or filename.startswith("."):
        raise ValueError("file name must be a plain name, without folders")
    if Path(filename).suffix.lower() not in UPLOAD_SUFFIXES:
        raise ValueError(f"{filename}: unsupported file type; use {', '.join(sorted(UPLOAD_SUFFIXES))}")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError(f"{filename} is larger than the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")
    with _reserved(roots, round_id) as state:
        source = roots.directory(round_id) / SOURCE_DIR
        target = source / filename
        if not target.exists() and sum(1 for _ in _uploads(source)) >= MAX_FILES:
            raise ValueError(f"a round can hold at most {MAX_FILES} files")
        # Dropping the same folder again changes nothing, so it mustn't cost a reviewer their decisions.
        changed = target.exists() and target.read_bytes() != content
        partial = source / f".{filename}.part"
        partial.write_bytes(content)
        partial.replace(target)
        if changed:
            _mark_import_out_of_date(roots, state, filename)
        return _describe(target, None)


def list_files(roots: RoundRoots, round_id: str) -> list[RoundFile]:
    get_round(roots, round_id)
    return [_describe(path, None) for path in _uploads(roots.directory(round_id) / SOURCE_DIR)]


def describe_file(roots: RoundRoots, round_id: str, filename: str, sheet: str | None) -> RoundFile:
    """One uploaded file's columns, for a chosen sheet of a workbook."""
    get_round(roots, round_id)
    matches = [path for path in _uploads(roots.directory(round_id) / SOURCE_DIR) if path.name == filename]
    if not matches:
        raise RoundNotFound(f"no uploaded file named {filename!r}")
    return _describe(matches[0], sheet)


def remove_file(roots: RoundRoots, round_id: str, filename: str) -> None:
    with _reserved(roots, round_id) as state:
        matches = [path for path in _uploads(roots.directory(round_id) / SOURCE_DIR) if path.name == filename]
        if not matches:
            raise RoundNotFound(f"no uploaded file named {filename!r}")
        matches[0].unlink()
        _mark_import_out_of_date(roots, state, filename)


def _mark_import_out_of_date(roots: RoundRoots, state: RoundState, filename: str) -> None:
    """A file the import read has changed, so everything built from it is stale: start again from the import.

    The stages would otherwise skip as finished and carry on from the old dump. The saved mapping stays on
    disk, ready to import again. Files the mapping doesn't use change nothing.
    """
    if _stage(state, "import").status != "done" or filename not in _mapped_files(roots, state.round_id):
        return
    _discard_progress(roots, state.round_id)
    state = _reset_stages(state)
    state.import_counts = {}
    state.warnings = [f"{filename} changed after it was imported. Import the source again before running this round."]
    _save(roots, state)


def _mapped_files(roots: RoundRoots, round_id: str) -> set[str]:
    """The uploaded files the round's saved mapping reads: its table and any labels files."""
    path = roots.directory(round_id) / SOURCE_DIR / "mapping.yaml"
    if not path.is_file():
        return set()
    mapping = SourceMapping.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    return {mapping.source, *(spec.labels for spec in mapping.columns.specs().values() if not isinstance(spec, str))}


def _uploads(source: Path) -> list[Path]:
    return sorted(
        path for path in source.iterdir()
        if path.is_file() and not path.name.startswith(".") and path.suffix.lower() in UPLOAD_SUFFIXES
    )


def _describe(path: Path, sheet: str | None) -> RoundFile:
    described = RoundFile(name=path.name, size=path.stat().st_size)
    try:
        described.sheets = sheet_names(path)
        described.columns, described.row_count, described.samples = inspect_table(path, sheet)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        described.error = str(exc)
    return described


def configure_round(
    roots: RoundRoots, round_id: str, mapping: SourceMapping, settings: RoundSettings,
) -> RoundState:
    """Import the mapped source and build the round's config; any earlier progress starts over.

    Everything the mapping names must be a file already uploaded to this round. Mapping errors
    (a missing column, a bad labels file, no usable rows) raise `ValueError` and change nothing
    the round had: the previous mapping's outputs stay until a new import succeeds.
    """
    with _reserved(roots, round_id) as state:
        return _configure(roots, state, mapping, settings)


def _configure(
    roots: RoundRoots, state: RoundState, mapping: SourceMapping, settings: RoundSettings,
) -> RoundState:
    _check_llm(settings)
    round_id = state.round_id
    directory = roots.directory(round_id)
    source = directory / SOURCE_DIR
    uploaded = {path.name for path in _uploads(source)}
    named = [mapping.source] + [
        spec.labels for spec in mapping.columns.specs().values() if not isinstance(spec, str)
    ]
    missing = [name for name in named if name not in uploaded]
    if missing:
        raise ValueError(f"not uploaded to this round: {missing}; it has {sorted(uploaded)}")

    staging = directory / ".import"
    # Written beside the final config, because its relative paths are worked out from its own folder.
    new_config = directory / ".config.new.yaml"
    shutil.rmtree(staging, ignore_errors=True)
    new_config.unlink(missing_ok=True)
    staging.mkdir()
    try:
        # Import into a staging folder first, so a mapping that fails leaves the last good one in place.
        mapping_path = source / "mapping.yaml"
        previous_mapping = mapping_path.read_bytes() if mapping_path.exists() else None
        mapping_path.write_text(
            yaml.safe_dump(mapping.model_dump(mode="json", exclude_none=True), sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        try:
            imported = import_source(
                mapping_path, out_dir=staging, baseline_snapshot=roots.integrations / state.baseline_snapshot,
                template_config=roots.template, round_config=new_config,
            )
        except Exception as exc:
            if previous_mapping is None:
                mapping_path.unlink(missing_ok=True)
            else:
                mapping_path.write_bytes(previous_mapping)
            if isinstance(exc, zipfile.BadZipFile | KeyError):  # a corrupt workbook, or a sheet it doesn't have
                raise ValueError(f"can't read {mapping.source}: {exc}") from exc
            raise
        _discard_progress(roots, round_id)
        for path in staging.iterdir():
            path.replace(directory / path.name)
        config_path = _config_path(roots, round_id)
        new_config.replace(config_path)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        new_config.unlink(missing_ok=True)

    # The moved dump's path was written into the config while it sat in the staging folder.
    _write_settings(roots, config_path, imported.name, settings, dump=imported.sql_path.name)

    state = _reset_stages(state)
    state.source_name, state.settings = imported.name, settings
    state.import_counts, state.warnings = imported.counts, imported.warnings
    stage = _stage(state, "import")
    stage.status, stage.finished_at = "done", utcnow()
    stage.summary = (
        f"{imported.counts.get('categories', 0)} categories, {imported.counts.get('specialities', 0)} specialities, "
        f"{imported.counts.get('tags', 0)} tags"
    )
    _save(roots, state)
    return state


def _write_settings(
    roots: RoundRoots, config_path: Path, source_name: str, settings: RoundSettings, *, dump: str,
) -> None:
    """Point the config at the moved dump and apply the round's switches."""
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    for dataset in config["datasets"]:
        if dataset["name"] == source_name:
            dataset["paths"] = [dump]
    for metric in config["metrics"]:
        if metric.get("id") != "saturation":
            continue
        params = metric["params"]
        for name in SATURATION_SETTINGS:
            value = getattr(settings, name)
            if value is not None:
                params[name] = value
        if not settings.judge:
            params.pop("judge", None)
        elif params.get("judge"):
            # Beside rounds/, so the same source reuses paid verdicts wherever the server was started from.
            params["judge"]["cache"] = str(roots.rounds.parent.resolve() / f"judge_cache_{source_name}.json")
    config_path.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")
    load_config(config_path)  # the same check `write_round_config` makes: fail now, not at the first stage


def _discard_progress(roots: RoundRoots, round_id: str) -> None:
    """Remove what an earlier mapping's stages produced; it no longer describes this round."""
    directory = roots.directory(round_id)
    for name in ("report.json", "case_a.json", "case_b.json", "case_b.csv", "case_b.checkpoint.json",
                 "decisions.json", "review_queue.json"):
        (directory / name).unlink(missing_ok=True)
    shutil.rmtree(roots.merge_runs / round_id, ignore_errors=True)


def _reset_stages(state: RoundState) -> RoundState:
    state.stages = [RoundStage(id=stage.id) for stage in state.stages]
    state.merge_counts, state.pending_sources, state.decisions_applied = {}, 0, True
    state.score_run_id, state.enriched_run_id = None, None
    return state


def start_round(roots: RoundRoots, round_id: str) -> tuple[RoundState, list[RoundStageId]]:
    """Claim the stages left before review; a failed or interrupted round resumes where it stopped.

    Returns the stages for `run_stages`, which the caller runs in the background. Finished
    stages aren't repeated, so a retry never pays for an LLM step that already succeeded.
    """
    with _lock:
        state = _claimable(roots, round_id)
        if _stage(state, "import").status != "done":
            raise RoundConflict("save this round's mapping before running it")
        _check_llm(state.settings)
        _check_judge_cache(roots, state)
        stages = [stage for stage in PIPELINE_STAGES if _stage(state, stage).status not in FINISHED]
        if not stages:
            raise RoundConflict("this round has already run; review its tags or integrate it")
        return _claim(roots, state, stages), stages


def _claimable(roots: RoundRoots, round_id: str) -> RoundState:
    path = roots.directory(round_id) / "round.json"
    if not path.is_file():
        raise RoundNotFound(f"no round named {round_id!r}")
    if round_id in _active:
        raise RoundConflict("this round is already running or being changed")
    return _without_interrupted(RoundState.model_validate_json(path.read_text(encoding="utf-8")))


def _claim(roots: RoundRoots, state: RoundState, stages: list[RoundStageId]) -> RoundState:
    """Mark the round active and its next stage running, so the first status poll already shows it."""
    _active.add(state.round_id)
    for stage in state.stages:
        if stage.status == "running":  # left over from an interrupted server
            stage.status = "failed"
    first = _stage(state, stages[0])
    first.status, first.started_at, first.finished_at, first.error = "running", utcnow(), None, None
    _save(roots, state)
    return state


def run_stages(roots: RoundRoots, round_id: str, stages: list[RoundStageId]) -> None:
    """Run claimed stages in order, stopping at the first failure. Never raises: it runs in the background."""
    try:
        for stage_id in stages:
            state = RoundState.model_validate_json((roots.directory(round_id) / "round.json").read_text("utf-8"))
            stage = _stage(state, stage_id)
            stage.status, stage.started_at, stage.finished_at, stage.error = "running", utcnow(), None, None
            _save(roots, state)
            try:
                outcome = _HANDLERS[stage_id](roots, state)
                stage.status, stage.summary = outcome
            except Exception as exc:  # recorded on the round: this is the only place a failure can be reported
                stage.status, stage.error = "failed", f"{type(exc).__name__}: {exc}"
            stage.finished_at = utcnow()
            _save(roots, state)
            if stage.status == "failed":
                return
    finally:
        with _lock:
            _active.discard(round_id)


def _score(roots: RoundRoots, state: RoundState) -> tuple[str, str]:
    directory = roots.directory(state.round_id)
    _, report = run_config(directory / "config.yaml")
    state.score_run_id = report.run_id
    if report.status != "done":
        raise ValueError(report.error or "the scoring run failed")
    write_report(report, directory / "report.json")
    saturation = next(result for result in report.results if result.metric_id == "saturation")
    if saturation.status != "ok":
        raise ValueError(saturation.summary or "saturation did not run successfully")
    return "done", saturation.summary


def _routed(roots: RoundRoots, state: RoundState, parent_status: str) -> int:
    report = load_saturation(roots.directory(state.round_id) / "report.json", state.source_name or "")
    return len(report.tag_ids(ROUTED_ACTIONS, parent_status))


def _case_a(roots: RoundRoots, state: RoundState) -> tuple[str, str]:
    if not _routed(roots, state, "HAS_PARENT"):
        return "skipped", "No new tags with a parent to place"
    directory = roots.directory(state.round_id)
    records = use_cases.case_a(
        directory / "config.yaml", directory / "report.json", baseline=ROUND_BASELINE, incoming=state.source_name,
        output=directory / "case_a.json", semantic_model=SEMANTIC_MODEL,
    )
    resolved = sum(record["category"] is not None for record in records)
    return "done", f"{len(records)} placements proposed ({resolved} resolved)"


def _case_b(roots: RoundRoots, state: RoundState) -> tuple[str, str]:
    parentless = _routed(roots, state, "NO_PARENT")
    if not parentless:
        return "skipped", "No new tags without a parent"
    if not state.settings.case_b:
        message = f"Left out {parentless} new tag(s) without a parent: LLM classification is off for this round"
        state.warnings.append(message)
        return "skipped", message
    directory = roots.directory(state.round_id)
    records = use_cases.case_b(
        directory / "config.yaml", directory / "report.json", baseline=ROUND_BASELINE, incoming=state.source_name,
        output=directory / "case_b.json", provider="tag_context",
        context=directory / f"{state.source_name}_tag_context.json", checkpoint=directory / "case_b.checkpoint.json",
    )
    return "done", f"{len(records)} placements proposed by the LLM"


def _merge(roots: RoundRoots, state: RoundState) -> tuple[str, str]:
    directory = roots.directory(state.round_id)
    inputs = [path for path in (directory / "case_a.json", directory / "case_b.json") if path.is_file()]
    if not inputs:
        raise ValueError("no tags to merge: saturation found nothing new in this source")
    decisions = directory / "decisions.json"
    run = use_cases.merge_preview(
        directory / "config.yaml", inputs, baseline=ROUND_BASELINE, out_dir=roots.merge_runs / state.round_id,
        decisions=decisions if decisions.is_file() else None, actor=state.reviewer or "dashboard",
    )
    if not run.ok:
        problems = list(run.validation.input_errors.values()) or ["the preview failed its structural checks"]
        raise ValueError("; ".join(problems))
    counts = run.preview.counts
    state.merge_counts = counts.model_dump()
    state.pending_sources = counts.pending_sources
    state.decisions_applied = True
    state.warnings = _unique([*state.warnings, *run.warnings])
    return "done", (
        f"{counts.added_tags} new tags, {counts.reused_tags} reused, {counts.pending_sources} waiting for review"
    )


def _integrate(roots: RoundRoots, state: RoundState) -> tuple[str, str]:
    directory = roots.directory(state.round_id)
    outcome = use_cases.integrate(
        directory / "config.yaml", roots.merge_runs / state.round_id, baseline=ROUND_BASELINE,
        out_dir=roots.integrations / state.round_id, actor=state.reviewer or "dashboard",
    )
    state.warnings = _unique([*state.warnings, *outcome.warnings])
    counts = outcome.integration.counts
    return "done", (
        f"{counts['categories']} new categories, {counts['specialities']} new specialities, {counts['tags']} new tags"
    )


def _score_enriched(roots: RoundRoots, state: RoundState) -> tuple[str, str]:
    _, report = run_config(roots.integrations / state.round_id / "enriched.yaml")
    state.enriched_run_id = report.run_id
    if report.status != "done":
        raise ValueError(report.error or "the scoring run failed")
    return "done", "Scored the enriched taxonomy with the round's metrics"


_HANDLERS = {
    "score": _score, "case_a": _case_a, "case_b": _case_b, "merge": _merge,
    "integrate": _integrate, "score_enriched": _score_enriched,
}


def delete_round(roots: RoundRoots, round_id: str) -> None:
    """Remove a dashboard round and what it produced, unless it is running or a later round builds on it.

    Only rounds with a `round.json` qualify, so hand-run rounds can't be removed from here. Stored runs
    and audit events stay: the first is history, and the second is append-only by design.
    """
    with _lock:
        _claimable(roots, round_id)
        snapshot = roots.integrations / round_id
        if snapshot.is_dir():
            built = {
                item.name for item in read_rounds(roots.integrations, roots.merge_runs)
                if item.name != round_id and item.baseline == _fingerprint(snapshot)
            }
            if built:
                raise RoundConflict(f"can't remove {round_id!r}: {sorted(built)} are built on its snapshot")
            active = [
                other.round_id for other in list_rounds_unlocked(roots)
                if other.round_id != round_id and other.baseline_snapshot == round_id
            ]
            if active:
                raise RoundConflict(f"can't remove {round_id!r}: round(s) {active} are built on its snapshot")
        shutil.rmtree(snapshot, ignore_errors=True)
        shutil.rmtree(roots.merge_runs / round_id, ignore_errors=True)
        shutil.rmtree(roots.directory(round_id))


def list_rounds_unlocked(roots: RoundRoots) -> list[RoundState]:
    """Every readable round's saved state, for callers already holding the lock."""
    states = []
    for path in roots.rounds.glob("*/round.json"):
        try:
            states.append(RoundState.model_validate_json(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return states


def _fingerprint(snapshot: Path) -> Any:
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    return file_fingerprint([*(snapshot / name for name in manifest["baseline"]["files"]), snapshot / "additions.sql"])


def _check_llm(settings: RoundSettings) -> None:
    if not (settings.judge or settings.case_b):
        return
    allowed, key_configured = llm_status()
    if not allowed:
        raise ValueError("LLM steps are off on this server; set TAXONOMY_ALLOW_LLM=true in backend/.env to allow them")
    if not key_configured:
        raise ValueError(f"LLM steps need a real OPENAI_API_KEY in {env.ENV_FILE}")


def _check_judge_cache(roots: RoundRoots, state: RoundState) -> None:
    """Rounds of one source share a judge cache, and a judge rewrites the whole file: two at once lose paid answers."""
    if not state.settings.judge:
        return
    for other in list_rounds_unlocked(roots):
        if (
            other.round_id != state.round_id and other.round_id in _active and other.settings.judge
            and other.source_name == state.source_name
        ):
            raise RoundConflict(f"round {other.round_id!r} is using this source's judge cache; wait for it to finish")


def _require_editable(state: RoundState) -> None:
    if any(stage.status == "running" for stage in state.stages):
        raise RoundConflict("this round is running; wait for it to stop before changing it")
    if _stage(state, "integrate").status == "done":
        raise RoundConflict("this round is already integrated; start a new round instead")


def _stage(state: RoundState, stage_id: RoundStageId) -> RoundStage:
    return next(stage for stage in state.stages if stage.id == stage_id)


def _config_path(roots: RoundRoots, round_id: str) -> Path:
    return roots.directory(round_id) / "config.yaml"


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _save(roots: RoundRoots, state: RoundState) -> None:
    path = roots.directory(state.round_id) / "round.json"
    partial = path.with_name("round.json.tmp")
    partial.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    partial.replace(path)
