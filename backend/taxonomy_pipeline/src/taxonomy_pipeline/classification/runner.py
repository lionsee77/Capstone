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

from ..datasets.base import TaxonomyDataset
from .deduplication import deduplicate_outputs
from .export import export_preview_csv
from .models import BatchValidationReport, MergePreview, ReviewDecision
from .preview import build_preview
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


def check_saturation_runs(payloads: dict[str, str]) -> list[str]:
    """Refuse inputs stamped with different saturation runs; warn about unstamped ones."""
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
    if len(run_ids) > 1:
        detail = "; ".join(f"{run_id}: {', '.join(sorted(names))}" for run_id, names in sorted(run_ids.items()))
        raise MixedSaturationRunsError(f"Inputs come from different saturation runs ({detail}). Rerun Case A and "
                                       "Case B against one saturation report.")
    return warnings


def _input_names(paths: list[Path]) -> dict[Path, str]:
    names = {path: path.name for path in paths}
    if len(set(names.values())) != len(names):
        names = {path: str(path) for path in paths}
    return names


def run_merge_preview(
    inputs: list[str | Path],
    baseline: TaxonomyDataset,
    *,
    out_dir: str | Path,
    legacy_case_a: list[str | Path] = (),
    legacy_case_b: dict[str | Path, str] | None = None,
    skip_case_a_handoffs: bool = False,
    decisions: list[ReviewDecision] = (),
    semantic_matcher: object | None = None,
) -> MergeRun:
    """Chain the stages over producer files and write validation, deduplication,
    resolution and the preview CSV to `out_dir`.

    `inputs` follow the nine-field contract; `legacy_case_a` and `legacy_case_b`
    (path -> source dataset name) go through the temporary intake adapters.
    """
    legacy_case_b = {Path(path): source for path, source in (legacy_case_b or {}).items()}
    paths = [Path(path) for path in [*inputs, *legacy_case_a, *legacy_case_b]]
    if len(set(paths)) != len(paths):
        raise ValueError("Each input file may be given only once")
    names = _input_names(paths)
    payloads = {names[path]: path.read_text(encoding="utf-8") for path in paths}
    warnings = check_saturation_runs(payloads)

    validation = validate_outputs(
        payloads, baseline,
        case_a_inputs={names[Path(path)] for path in legacy_case_a},
        case_b_sources={names[path]: source for path, source in legacy_case_b.items()},
        skip_case_a_handoffs=skip_case_a_handoffs,
    )
    deduplication = deduplicate_outputs(validation, semantic_matcher=semantic_matcher)
    resolution = resolve_outputs(deduplication, baseline, list(decisions))
    preview = build_preview(resolution, baseline)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for filename, report in (
        ("validation.json", validation), ("deduplication.json", deduplication), ("resolution.json", resolution),
    ):
        (out_dir / filename).write_text(report.model_dump_json(indent=2), encoding="utf-8")
    export_preview_csv(preview, out_dir / "preview.csv")
    return MergeRun(out_dir=out_dir, validation=validation, preview=preview, warnings=warnings)
