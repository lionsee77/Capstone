"""Enrichment use cases: place new tags (Case A, Case B), merge and review them, integrate the result.

Each function takes what the matching CLI command takes - a config path and
dataset names rather than loaded datasets - so the CLI only parses flags, and
does everything else the command does: loading, checks, writing outputs and
collecting warnings.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import load_config
from ..enrichment.audit import EVENTS
from ..enrichment.bulk_decisions import BulkDraft, draft_decisions
from ..enrichment.case_a import run_case_a
from ..enrichment.case_b import run_case_b, validate_context_argument
from ..enrichment.integrate import Integration, IntegrationError
from ..enrichment.integrate import integrate as integrate_snapshot
from ..enrichment.models import ProposalReviewReport
from ..enrichment.proposals.models import ScreeningSettings
from ..enrichment.review_view import load_review_view
from ..enrichment.routing import load_saturation
from ..enrichment.runner import REVIEW_DECISIONS, MergeRun, load_decisions, load_merge_run, run_merge_preview
from .scoring import load_taxonomies

# merge-preview's hierarchy screening thresholds when the caller gives none; adapters show these as defaults.
SCREENING_DEFAULTS = ScreeningSettings()


# Reject blank actor input before any use-case reads or writes and normalize attribution once.
def _validated_actor(actor: str) -> str:
    actor = actor.strip()
    if not actor:
        raise ValueError("actor must not be blank")
    return actor


def _timestamped(parent: str) -> str:
    return f"{parent}/{datetime.now():%Y%m%d-%H%M%S}"


def case_a(
    config_path: str | Path, report: str | Path, *, baseline: str, incoming: str, output: str | Path,
    semantic_model: str | None = None, category_scoped_semantic_threshold: float | None = None,
    one_category_per_tag: bool = False,
) -> list[dict[str, Any]]:
    """Place the incoming tags saturation routed to Case A and write them to `output`."""
    records = run_case_a(
        load_taxonomies(config_path, baseline, incoming), report,
        baseline=baseline, incoming=incoming, semantic_model=semantic_model or None,
        category_scoped_semantic_threshold=category_scoped_semantic_threshold,
        one_category_per_tag=one_category_per_tag,
    )
    Path(output).write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    return records


def case_b(
    config_path: str | Path, report: str | Path, *, baseline: str, incoming: str, output: str | Path,
    provider: str | None = None, context: str | Path | None = None, checkpoint: str | Path | None = None,
    batch_size: int = 5, model: str | None = None,
) -> list[dict[str, Any]]:
    """Classify routed tags with the generic JSON context provider unless another is selected."""
    validate_context_argument(context)
    datasets = load_taxonomies(config_path, baseline, incoming)
    return asyncio.run(run_case_b(
        datasets, report, baseline=baseline, incoming=incoming, provider=provider or "tag_context", context=context,
        output=output,
        checkpoint=checkpoint, batch_size=batch_size, model=model,
    ))


# Validate submission inputs before loading sources and running the merge workflow.
def merge_preview(
    config_path: str | Path, inputs: list[str | Path], *, baseline: str, out_dir: str | Path | None = None,
    decisions: str | Path | None = None, semantic_model: str | None = None,
    screening_lexical: float | None = None, screening_semantic: float | None = None,
    change_log: str | Path | None = None,
    actor: str = "unspecified",
) -> MergeRun:
    """Validate, deduplicate and resolve producer outputs into `out_dir` (default `merge_runs/<timestamp>`)."""
    actor = _validated_actor(actor)
    from ..matching.semantic import SemanticMatcher

    if not inputs:
        raise ValueError("give at least one --input file")
    screening_settings = ScreeningSettings(
        lexical_threshold=SCREENING_DEFAULTS.lexical_threshold if screening_lexical is None else screening_lexical,
        semantic_threshold=SCREENING_DEFAULTS.semantic_threshold if screening_semantic is None else screening_semantic,
    )
    taxonomy = load_taxonomies(config_path, baseline)[baseline]
    result = run_merge_preview(
        list(inputs), taxonomy, out_dir=out_dir or _timestamped("merge_runs"),
        decisions=load_decisions(decisions) if decisions else [],
        semantic_matcher=SemanticMatcher(semantic_model) if semantic_model else None,
        screening_settings=screening_settings, change_log=change_log, actor=actor,
    )
    if result.audit_delivery_error:
        result.warnings.append(f"Merge preview saved at {result.out_dir}. {result.audit_delivery_error}")
    return result


def review_proposals(
    merge_run: str | Path, *, proposal_id: str | None = None, level: str | None = None, offset: int = 0,
    limit: int = 50,
) -> ProposalReviewReport:
    """Read a saved merge run's screened proposals and decisions without changing anything."""
    return load_review_view(
        merge_run, proposal_id=proposal_id, level=level, offset=offset, limit=1 if proposal_id else limit,
    )


def bulk_decide(
    config_path: str | Path, merge_run: str | Path, report: str | Path, *, baseline: str, incoming: str,
    output: str | Path, create_below: float | None = None, reuse_above: float | None = None,
    decisions: str | Path | None = None,
) -> BulkDraft:
    """Draft decisions for a merge run's clear-cut ambiguous tags and write every decision to `output`.

    Refuses to overwrite `output` unless it is also `decisions`, and refuses a
    `report` other than the saturation run the merge run's inputs came from.
    """
    merge_run, output = Path(merge_run), Path(output)
    if output.exists() and (not decisions or output.resolve() != Path(decisions).resolve()):
        raise ValueError(f"{output} already exists; pass it as --decisions to extend it, or pick another --output")
    resolution, info = load_merge_run(merge_run)
    saturation = load_saturation(report, incoming)
    run_ids = info.saturation_run_ids if info else []
    if not run_ids:
        raise ValueError(f"{merge_run} records no saturation run (unstamped inputs, or written before run.json "
                         "had one), so its tags can't be tied to a report; rerun the producers and merge-preview")
    if run_ids != [saturation.run_id]:
        raise ValueError(f"{merge_run} came from saturation run(s) {run_ids}, but {report} is "
                         f"{saturation.run_id!r}; use the report the producers read")
    taxonomy = load_taxonomies(config_path, baseline)[baseline]
    draft = draft_decisions(
        resolution, saturation, taxonomy, create_below=create_below, reuse_at_or_above=reuse_above,
        existing=load_decisions(decisions) if decisions else [],
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(REVIEW_DECISIONS.dump_json(draft.decisions, indent=2, exclude_defaults=True) + b"\n")
    return draft


@dataclass
class IntegrateResult:
    """The written snapshot, plus anything about it a person should check before using it."""

    integration: Integration
    warnings: list[str] = field(default_factory=list)


# Return the published snapshot with audit delivery and taxonomy quality warnings.
def integrate(
    config_path: str | Path, merge_run: str | Path, *, baseline: str, out_dir: str | Path | None = None,
    actor: str = "unspecified",
) -> IntegrateResult:
    """Turn a fully reviewed merge run into an enriched snapshot (default `integrations/<timestamp>`)."""
    actor = _validated_actor(actor)
    merge_run = Path(merge_run)
    resolution, info = load_merge_run(merge_run)
    config = load_config(config_path)
    dataset = next((d for d in config.datasets if d.name == baseline), None)
    if dataset is None:
        raise IntegrationError(f"{config_path} declares no dataset named {baseline!r}")
    events_path = merge_run / "audit_events.json"
    review_events = EVENTS.validate_json(events_path.read_bytes()) if events_path.exists() else []
    result = integrate_snapshot(
        resolution, dataset, out_dir=out_dir or _timestamped("integrations"), scoring_config=config, actor=actor,
        provenance={
            "merge_run": str(merge_run), "scoring_config": str(config_path),
            **(info.model_dump(exclude_unset=True) if info else {}),
        },
        review_events=review_events,
    )
    warnings = []
    if result.audit_delivery_error:
        warnings.append(f"Snapshot saved at {result.out_dir}. {result.audit_delivery_error}")
    omitted = sum(result.manifest["omitted_records"].values())
    if omitted:
        warnings.append(f"{omitted} input record(s) were omitted before review and are not in the snapshot "
                        f"(by status: {result.manifest['omitted_records']}); see {merge_run}/validation.json")
    for table, rows in result.manifest["exact_duplicate_rows"].items():
        if rows["enriched"] > rows["baseline"]:
            warnings.append(f"{table}: rows in exact-duplicate name clusters rose from {rows['baseline']} to "
                            f"{rows['enriched']}; check the new names before using this snapshot")
    return IntegrateResult(integration=result, warnings=warnings)
