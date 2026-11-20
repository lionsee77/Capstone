"""Lineage use case: how the taxonomy grew round by round, and which stored runs scored each version.

Behind `GET /lineage`, which the dashboard's improvement page reads so that it
hard-codes no run, stage or source: it picks a chain and its best run here,
then compares consecutive stages through `GET /runs/{before}/compare/{after}`
(`app.comparison`), passing each stage's dataset name as the subject.

Stages and their order come from the integration rounds on disk
(`enrichment.lineage.read_rounds`). A run is linked to a stage when one of its
datasets loads exactly that stage's files, recognised by content
(`file_fingerprint`), so a run that read the original dump and one that read
the round's copy of it both count. Only datasets a scoring metric names as its
subject count: a run that loads a stage just as saturation's incoming source
has no scores for it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import db
from ..enrichment.lineage import FileFingerprint, IntegrationRound, file_fingerprint, read_rounds
from ..models import LineageChain, LineageRun, LineageStage, TaxonomyLineage
from ..quality.registry import METRICS
from .rounds import ORIGINAL_DUMPS
from .sources import ROUND_BASELINE

# The name of a chain's root when it is the client's original taxonomy.
ORIGINAL_ROOT = "original"

# How many of the newest runs are checked for lineage stages. Run history is small (one row per
# scoring run), and each run's files are hashed once and then cached.
RUN_LIMIT = 200


def taxonomy_lineage(
    integrations_root: Path, merge_root: Path, *, run_limit: int = RUN_LIMIT, original: Path = ORIGINAL_DUMPS,
) -> TaxonomyLineage:
    """The stages under `integrations_root`, the chains through them and the runs that scored them.

    `original` is the folder of the client's dumps, used to recognise a chain that started from them.
    """
    rounds = read_rounds(integrations_root, merge_root)
    dumps = sorted(original.glob("*.sql")) if original.is_dir() else []
    stages, fingerprints = _stages(rounds, file_fingerprint(dumps) if dumps else None)
    runs = _runs(fingerprints, run_limit)
    chains = [LineageChain(stages=path, runs=_best_runs(path, runs)) for path in _chains(stages)]
    # A chain with scores first (the sort keeps the rest in order), so a new unscored round can't replace a report.
    chains.sort(key=lambda chain: not chain.runs)
    return TaxonomyLineage(stages=stages, chains=chains, runs=runs)


def _stages(
    rounds: list[IntegrationRound], original: FileFingerprint | None,
) -> tuple[list[LineageStage], dict[FileFingerprint, str]]:
    """One stage per round's snapshot, plus a root for every baseline no round produced.

    A round calls whatever it built on `round_baseline`, enriched or not, so a root is named "original" only when its
    files are the client's dumps. An enriched snapshot whose parent can't be read keeps its own name.
    """
    produced = {item.snapshot: item.name for item in rounds}
    roots: dict[FileFingerprint, LineageStage] = {}
    for item in sorted(rounds, key=_created):
        if item.baseline not in produced and item.baseline not in roots:
            started_from_original = item.baseline_dataset == ROUND_BASELINE and original is not None \
                and item.baseline == original
            dataset = ORIGINAL_ROOT if started_from_original else item.baseline_dataset
            stage_id = _root_id(dataset, {stage.id for stage in roots.values()})
            roots[item.baseline] = LineageStage(id=stage_id, name=dataset)
    parents = {**{fingerprint: stage.id for fingerprint, stage in roots.items()}, **produced}
    stages = list(roots.values()) + [
        LineageStage(
            id=item.name, name=item.name, parent=parents[item.baseline], sources=item.sources,
            created_at=item.created_at, added=item.added,
        )
        for item in sorted(rounds, key=_created)
    ]
    return stages, parents


def _root_id(dataset: str, taken: set[str]) -> str:
    stage_id, suffix = f"baseline:{dataset}", 2
    while stage_id in taken:
        stage_id, suffix = f"baseline:{dataset}-{suffix}", suffix + 1
    return stage_id


def _created(item: IntegrationRound) -> tuple[str, str]:
    return (item.created_at.isoformat() if item.created_at else "", item.name)


def _chains(stages: list[LineageStage]) -> list[list[str]]:
    """Every root-to-leaf path, longest first, then the one whose last round is newest."""
    children: dict[str | None, list[LineageStage]] = {}
    for stage in stages:
        children.setdefault(stage.parent, []).append(stage)
    by_id = {stage.id: stage for stage in stages}
    paths: list[list[str]] = []

    def walk(stage: LineageStage, path: list[str]) -> None:
        path = [*path, stage.id]
        if stage.id not in children:
            paths.append(path)
        for child in children.get(stage.id, []):
            walk(child, path)

    for root in children.get(None, []):
        walk(root, [])
    newest = {path[-1]: by_id[path[-1]].created_at.isoformat() if by_id[path[-1]].created_at else "" for path in paths}
    return sorted(paths, key=lambda path: (len(path), newest[path[-1]]), reverse=True)


def _runs(fingerprints: dict[FileFingerprint, str], limit: int) -> list[LineageRun]:
    """Finished runs that score at least one stage (a subject dataset loading its files), newest first."""
    sizes = {len(fingerprint) for fingerprint in fingerprints}
    runs = []
    for record in db.list_run_history(limit=limit):
        if record.status != "done":
            continue
        config = json.loads(record.config_json)
        subjects = _subjects(config)
        datasets: dict[str, str] = {}
        for dataset in config.get("datasets", []):
            if dataset.get("name") not in subjects:
                continue
            paths = dataset.get("paths") or []
            if len(paths) not in sizes:  # can't match any stage; don't hash reference workbooks
                continue
            stage = fingerprints.get(file_fingerprint(paths) or ())
            if stage is not None and stage not in datasets:
                datasets[stage] = dataset["name"]
        if datasets:
            runs.append(LineageRun(
                run_id=record.run_id, created_at=record.created_at, judged=_judged(config), datasets=datasets,
            ))
    return runs


def _subjects(config: dict[str, Any]) -> set[str]:
    """The datasets the config's scoring metrics score: each metric's `subject_param` value."""
    subjects = set()
    for metric in config.get("metrics", []):
        registered = METRICS.get(metric.get("id"))
        param = registered.subject_param if registered else None
        subject = (metric.get("params") or {}).get(param) if param else None
        if isinstance(subject, str):
            subjects.add(subject)
    return subjects


def _judged(config: dict[str, Any]) -> bool:
    return any(
        metric.get("id") == "saturation" and (metric.get("params") or {}).get("judge") is not None
        for metric in config.get("metrics", [])
    )


def _best_runs(path: list[str], runs: list[LineageRun]) -> list[str]:
    """Runs scoring at least two of the chain's stages: most stages, then judged, then newest."""
    covering = [(sum(stage in run.datasets for stage in path), run) for run in runs]
    ranked = sorted(
        (item for item in covering if item[0] >= 2),
        key=lambda item: (item[0], item[1].judged, item[1].created_at), reverse=True,
    )
    return [run.run_id for _, run in ranked]
