"""Run the merge stages end to end: validate -> deduplicate -> resolve -> preview -> CSV.

Each stage stays an independent, separately tested function; this module only
chains them and writes every intermediate report to one directory so a reviewer
can see why a row ended up where it did. Review is a loop, not a single run:
the first run lists duplicate candidates as pending, a reviewer records
`ReviewDecision`s in a JSON file, and the same command is rerun with it.

Producer outputs stamped with `saturation_run_id` (see `routing.py`) must all
come from the same saturation run, otherwise Case A and Case B may have split
different sets of new tags.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import TypeAdapter

from ..matching.semantic import SimilarityMatcher
from ..taxonomy.base import TaxonomyDataset
from .audit import record_review_changes
from .change_log import append_review_decisions
from .deduplication import deduplicate_outputs
from .export import export_preview_csv
from .models import BatchValidationReport, MergePreview, MergeRunInfo, ResolutionReport, ReviewDecision
from .preview import build_preview
from .proposals.models import ScreeningSettings
from .resolution import resolve_outputs
from .routing import RUN_ID_FIELD
from .validation import validate_outputs

REVIEW_DECISIONS = TypeAdapter(list[ReviewDecision])


class MixedSaturationRunsError(ValueError):
    """Inputs were produced from different saturation runs and must not be combined."""


@dataclass
class MergeRun:
    """What one run produced; `ok` is false when an input was unreadable or the batch was blocked."""

    out_dir: Path
    validation: BatchValidationReport
    preview: MergePreview
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.validation.complete and not self.preview.blocked


def load_decisions(path: str | Path) -> list[ReviewDecision]:
    return REVIEW_DECISIONS.validate_json(Path(path).read_text(encoding="utf-8"))


def load_merge_run(merge_run: str | Path) -> tuple[ResolutionReport, MergeRunInfo | None]:
    """A saved merge run's resolution report, and its `run.json` (None for runs written before it existed)."""
    merge_run = Path(merge_run)
    resolution = ResolutionReport.model_validate_json((merge_run / "resolution.json").read_text(encoding="utf-8"))
    run_info = merge_run / "run.json"
    if not run_info.exists():
        return resolution, None
    return resolution, MergeRunInfo.model_validate_json(run_info.read_text(encoding="utf-8"))


def saturation_run_ids(payloads: dict[str, str]) -> list[str]:
    """The distinct `saturation_run_id` stamps across inputs (more than one only before the check refuses)."""
    return sorted(_run_ids_by_input(payloads)[0])


def check_saturation_runs(payloads: dict[str, str]) -> list[str]:
    """Refuse inputs stamped with different saturation runs; warn about unstamped ones."""
    run_ids, warnings = _run_ids_by_input(payloads)
    if len(run_ids) > 1:
        detail = "; ".join(f"{run_id}: {', '.join(sorted(names))}" for run_id, names in sorted(run_ids.items()))
        raise MixedSaturationRunsError(f"Inputs come from different saturation runs ({detail}). Rerun Case A and "
                                       "Case B against one saturation report.")
    return warnings


def _run_ids_by_input(payloads: dict[str, str]) -> tuple[dict[str, set[str]], list[str]]:
    run_ids: dict[str, set[str]] = {}
    warnings = []
    for name, payload in payloads.items():
        try:
            rows = json.loads(payload)
        except ValueError:
            continue  # validation reports unreadable inputs itself
        if not isinstance(rows, list):
            continue
        stamps = [row.get(RUN_ID_FIELD) if isinstance(row, dict) else None for row in rows]
        if rows and any(stamp is None for stamp in stamps):
            warnings.append(f"{name}: records without {RUN_ID_FIELD}; cannot confirm which saturation run they used")
        for stamp in stamps:
            if stamp is not None:
                run_ids.setdefault(str(stamp), set()).add(name)
    return run_ids, warnings


def _input_names(paths: list[Path]) -> dict[Path, str]:
    names = {path: path.name for path in paths}
    if len(set(names.values())) != len(names):
        names = {path: str(path) for path in paths}
    return names


# Run the shared merge stages with optional screening settings and persist their review evidence.
def run_merge_preview(
    inputs: list[str | Path],
    baseline: TaxonomyDataset,
    *,
    out_dir: str | Path,
    decisions: list[ReviewDecision] = (),
    semantic_matcher: SimilarityMatcher | None = None,
    screening_settings: ScreeningSettings | None = None,
    change_log: str | Path | None = None,
    actor: str = "unspecified",
) -> MergeRun:
    """Chain the stages over producer files and write validation, deduplication,
    resolution and the preview CSV to `out_dir`.

    `inputs` follow the nine-field contract. Decisions not yet in `change_log` (default `out_dir/change_log.jsonl`) are
    appended to it; point several runs at one log to keep a reviewer's history.
    """
    paths = [Path(path) for path in inputs]
    if len(set(paths)) != len(paths):
        raise ValueError("Each input file may be given only once")
    names = _input_names(paths)
    payloads = {names[path]: path.read_text(encoding="utf-8") for path in paths}
    warnings = check_saturation_runs(payloads)

    validation = validate_outputs(payloads, baseline)
    deduplication = deduplicate_outputs(validation, semantic_matcher=semantic_matcher)
    resolution = resolve_outputs(
        deduplication, baseline, list(decisions), semantic_matcher=semantic_matcher,
        screening_settings=screening_settings,
    )
    preview = build_preview(resolution, baseline)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for filename, report in (
        ("validation.json", validation), ("deduplication.json", deduplication), ("resolution.json", resolution),
    ):
        (out_dir / filename).write_text(report.model_dump_json(indent=2), encoding="utf-8")
    export_preview_csv(preview, out_dir / "preview.csv")
    run_info = MergeRunInfo(
        saturation_run_ids=saturation_run_ids(payloads),
        inputs=[str(path) for path in paths],
        decisions=len(decisions),
        baseline_snapshot_id=resolution.baseline_snapshot_id,
        blocked=preview.blocked,
        pending_sources=preview.counts.pending_sources,
    )
    # json.dumps, not model_dump_json, so run.json keeps its existing formatting (ASCII-escaped).
    (out_dir / "run.json").write_text(json.dumps(run_info.model_dump(), indent=2), encoding="utf-8")
    log_path = Path(change_log) if change_log else out_dir / "change_log.jsonl"
    _, entries = append_review_decisions(
        log_path, resolution.decisions,
        baseline_snapshot_id=resolution.baseline_snapshot_id, actor=actor,
    )
    record_review_changes(resolution, entries, out_dir)
    return MergeRun(out_dir=out_dir, validation=validation, preview=preview, warnings=warnings)
