"""`taxonomy-pipeline run|serve|case-a|case-b|merge-preview|bulk-decide|integrate`

Every command is a thin wrapper around one library function, so the CLI never
holds logic of its own: `run` calls `pipeline.run_pipeline` - the exact same
function the FastAPI `/runs` route calls - plus optional SQLite persistence and
a JSON report on stdout or a file. The classification commands call
`classification.case_a.run_case_a`, `classification.case_b.run_case_b`,
`classification.runner.run_merge_preview`, `classification.bulk_decisions.draft_decisions`
and `classification.integrate.integrate`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from . import db
from .config import load_config
from .datasets.base import LoadedDataset, TaxonomyDataset
from .pipeline import load_datasets, run_pipeline
from .proposals.models import ScreeningSettings

SKILLSFUTURE_WORKBOOK = (
    Path(__file__).resolve().parents[3] / "skillsfuture" / "jobsandskills-skillsfuture-unique-skills-list.xlsx"
)


def _cmd_run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    report = run_pipeline(config)

    if not args.no_save:
        db.save_run(report, config_json=config.model_dump_json())

    output = report.model_dump_json(indent=2)
    if args.output:
        Path(args.output).write_text(output)
        print(f"wrote report to {args.output}")
    else:
        print(output)

    for result in report.results:
        print(f"[{result.status.upper()}] {result.metric_id}: {result.summary}", file=sys.stderr)
    if report.error:
        print(f"[FAILED] {report.error}", file=sys.stderr)

    # "done" only means the run completed - it says nothing about whether
    # every metric in it succeeded, so a CI gate needs both checked.
    any_metric_errors = any(result.status == "error" for result in report.results)
    return 0 if report.status == "done" and not any_metric_errors else 1


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("taxonomy_pipeline.api.app:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def _datasets(args: argparse.Namespace, *names: str) -> dict[str, LoadedDataset]:
    """Load only the datasets this command uses, so unrelated reference workbooks aren't required."""
    config = load_config(args.config)
    config.datasets = [dataset for dataset in config.datasets if dataset.name in names]
    datasets = load_datasets(config)
    for name in names:
        if not isinstance(datasets.get(name), TaxonomyDataset):
            raise ValueError(f"{args.config} declares no taxonomy dataset named {name!r}")
    return datasets


def _cmd_case_a(args: argparse.Namespace) -> int:
    from .classification.case_a import run_case_a

    try:
        records = run_case_a(
            _datasets(args, args.baseline, args.incoming), args.report,
            baseline=args.baseline, incoming=args.incoming, semantic_model=args.semantic_model or None,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"[FAILED] case-a: {exc}", file=sys.stderr)
        return 1
    Path(args.output).write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    resolved = sum(record["category"] is not None for record in records)
    print(f"wrote {args.output}: {len(records)} records ({resolved} resolved, {len(records) - resolved} unresolved)")
    return 0


def _cmd_case_b(args: argparse.Namespace) -> int:
    from .classification.case_b import run_case_b

    try:
        asyncio.run(run_case_b(
            _datasets(args, args.baseline, args.incoming), args.report,
            baseline=args.baseline, incoming=args.incoming, unique_skills=args.unique, output=args.output,
            checkpoint=args.checkpoint, batch_size=args.batch_size, model=args.model, debug=args.debug_response,
        ))
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"[FAILED] case-b: {exc}", file=sys.stderr)
        return 1
    return 0


def _legacy_case_b(values: list[str]) -> dict[str, str]:
    pairs = {}
    for value in values:
        path, separator, source = value.rpartition("=")
        if not separator or not path or not source:
            raise ValueError(f"--legacy-case-b expects FILE=SOURCE_DATASET, got {value!r}")
        pairs[path] = source
    return pairs


def _cmd_merge_preview(args: argparse.Namespace) -> int:
    from .classification.runner import load_decisions, run_merge_preview
    from .metrics.saturation import SemanticMatcher

    out_dir = args.out_dir or f"merge_runs/{datetime.now():%Y%m%d-%H%M%S}"
    try:
        if not (args.input or args.legacy_case_a or args.legacy_case_b):
            raise ValueError("give at least one --input, --legacy-case-a or --legacy-case-b file")
        screening_settings = ScreeningSettings(
            lexical_threshold=args.screening_lexical, semantic_threshold=args.screening_semantic,
        )
        baseline = _datasets(args, args.baseline)[args.baseline]
        run = run_merge_preview(
            args.input, baseline, out_dir=out_dir,
            legacy_case_a=args.legacy_case_a, legacy_case_b=_legacy_case_b(args.legacy_case_b),
            skip_case_a_handoffs=args.skip_case_a_handoffs,
            decisions=load_decisions(args.decisions) if args.decisions else [],
            semantic_matcher=SemanticMatcher(args.semantic_model) if args.semantic_model else None,
            screening_settings=screening_settings,
        )
    except (OSError, ValueError) as exc:
        print(f"[FAILED] merge-preview: {exc}", file=sys.stderr)
        return 1

    for warning in run.warnings:
        print(f"[WARN] {warning}", file=sys.stderr)
    for name, error in run.validation.input_errors.items():
        print(f"[FAILED] {name}: {error}", file=sys.stderr)
    counts = run.preview.counts
    status = "blocked" if run.preview.blocked else "partial" if run.preview.partial else "complete"
    print(
        f"[{status.upper()}] {counts.added_tags} new tags, {counts.reused_tags} reused, "
        f"{counts.added_links} new links, "
        f"{counts.added_categories} new categories, {counts.added_specialities} new specialities; "
        f"{counts.pending_sources} sources pending review, {counts.omitted_records} records omitted",
        file=sys.stderr,
    )
    print(f"wrote {run.out_dir}/preview.csv (plus validation, deduplication and resolution JSON)")
    return 0 if run.ok else 1


def _cmd_bulk_decide(args: argparse.Namespace) -> int:
    from .classification.bulk_decisions import draft_decisions
    from .classification.models import ResolutionReport
    from .classification.routing import load_saturation
    from .classification.runner import REVIEW_DECISIONS, load_decisions

    merge_run, output = Path(args.merge_run), Path(args.output)
    try:
        if output.exists() and (not args.decisions or output.resolve() != Path(args.decisions).resolve()):
            raise ValueError(f"{output} already exists; pass it as --decisions to extend it, or pick another --output")
        resolution = ResolutionReport.model_validate_json((merge_run / "resolution.json").read_text(encoding="utf-8"))
        saturation = load_saturation(args.report, args.incoming)
        run_info = merge_run / "run.json"
        run_ids = (json.loads(run_info.read_text(encoding="utf-8")).get("saturation_run_ids", [])
                   if run_info.exists() else [])
        if not run_ids:
            raise ValueError(f"{merge_run} records no saturation run (unstamped inputs, or written before run.json "
                             "had one), so its tags can't be tied to a report; rerun the producers and merge-preview")
        if run_ids != [saturation.run_id]:
            raise ValueError(f"{merge_run} came from saturation run(s) {run_ids}, but {args.report} is "
                             f"{saturation.run_id!r}; use the report the producers read")
        baseline = _datasets(args, args.baseline)[args.baseline]
        draft = draft_decisions(
            resolution, saturation, baseline, create_below=args.create_below, reuse_at_or_above=args.reuse_above,
            existing=load_decisions(args.decisions) if args.decisions else [],
        )
    except (OSError, ValueError) as exc:
        print(f"[FAILED] bulk-decide: {exc}", file=sys.stderr)
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(REVIEW_DECISIONS.dump_json(draft.decisions, indent=2, exclude_defaults=True) + b"\n")
    counts = draft.counts
    print(
        f"[OK] drafted {counts['create']} create and {counts['reuse']} reuse decision(s); "
        f"{counts['left_for_review']} ambiguous tag(s) left for review, "
        f"{counts['held_for_another_reason']} held for another reason, {counts['not_ambiguous']} other pending, "
        f"{counts['already_decided']} already decided",
        file=sys.stderr,
    )
    print(f"wrote {output} ({len(draft.decisions)} decisions); apply it with merge-preview --decisions {output}")
    return 0


def _cmd_integrate(args: argparse.Namespace) -> int:
    from .classification.integrate import IntegrationError, integrate
    from .classification.models import ResolutionReport

    merge_run = Path(args.merge_run)
    out_dir = args.out_dir or f"integrations/{datetime.now():%Y%m%d-%H%M%S}"
    try:
        resolution = ResolutionReport.model_validate_json((merge_run / "resolution.json").read_text(encoding="utf-8"))
        run_info = merge_run / "run.json"
        provenance = json.loads(run_info.read_text(encoding="utf-8")) if run_info.exists() else {}
        baseline = next((d for d in load_config(args.config).datasets if d.name == args.baseline), None)
        if baseline is None:
            raise IntegrationError(f"{args.config} declares no dataset named {args.baseline!r}")
        result = integrate(
            resolution, baseline, out_dir=out_dir, provenance={"merge_run": str(merge_run), **provenance},
        )
    except (OSError, ValueError) as exc:
        print(f"[FAILED] integrate: {exc}", file=sys.stderr)
        return 1
    omitted = sum(result.manifest["omitted_records"].values())
    if omitted:
        print(f"[WARN] {omitted} input record(s) were omitted before review and are not in the snapshot "
              f"(by status: {result.manifest['omitted_records']}); see {merge_run}/validation.json", file=sys.stderr)
    for table, rows in result.manifest["exact_duplicate_rows"].items():
        if rows["enriched"] > rows["baseline"]:
            print(f"[WARN] {table}: rows in exact-duplicate name clusters rose from {rows['baseline']} to "
                  f"{rows['enriched']}; check the new names before using this snapshot", file=sys.stderr)
    counts = result.counts
    print(
        f"[OK] {counts['categories']} new categories, {counts['specialities']} new specialities, "
        f"{counts['tags']} new tags, {counts['speciality_tags']} new links",
        file=sys.stderr,
    )
    print(f"wrote {result.out_dir}/ (score it: taxonomy-pipeline run --config {result.out_dir}/enriched.yaml)")
    return 0


def _add_dataset_args(parser: argparse.ArgumentParser, *, incoming: bool = True) -> None:
    parser.add_argument("--config", default="configs/original_vs_skillsfuture.yaml", help="pipeline config (YAML)")
    parser.add_argument("--baseline", default="original", help="taxonomy dataset to place tags into")
    if incoming:
        parser.add_argument("--incoming", default="skillsfuture", help="taxonomy dataset the new tags come from")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="taxonomy_pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="run a pipeline config once")
    run_parser.add_argument("--config", required=True, help="path to a YAML pipeline config")
    run_parser.add_argument("--output", help="write the JSON report here instead of stdout")
    run_parser.add_argument("--no-save", action="store_true", help="don't persist this run to SQLite")
    run_parser.set_defaults(func=_cmd_run)

    serve_parser = subparsers.add_parser("serve", help="run the FastAPI app")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.add_argument("--reload", action="store_true")
    serve_parser.set_defaults(func=_cmd_serve)

    report_help = "saved `run` report whose saturation result decides which tags are new"
    case_a = subparsers.add_parser("case-a", help="place new tags that have their own source category")
    _add_dataset_args(case_a)
    case_a.add_argument("--report", default="report.json", help=report_help)
    case_a.add_argument("--semantic-model", default="all-MiniLM-L6-v2", help="sentence model; '' for lexical only")
    case_a.add_argument("--output", default="case_a_output.json")
    case_a.set_defaults(func=_cmd_case_a)

    case_b = subparsers.add_parser("case-b", help="propose placements for parentless new tags with an LLM")
    _add_dataset_args(case_b)
    case_b.add_argument("--report", default="report.json", help=report_help)
    case_b.add_argument("--unique", default=str(SKILLSFUTURE_WORKBOOK), help="SkillsFuture unique-skills workbook")
    case_b.add_argument("--output", default="case_b_output.json", help="JSON output; a CSV is written next to it")
    case_b.add_argument("--checkpoint", help="checkpoint file for resuming after a failed run")
    case_b.add_argument("--batch-size", type=int, default=5)
    case_b.add_argument("--model", help="OpenAI model name (default: $OPENAI_MODEL or gpt-4o-mini)")
    case_b.add_argument("--debug-response", action="store_true", help="print up to 4,000 characters per response")
    case_b.set_defaults(func=_cmd_case_b)

    merge = subparsers.add_parser("merge-preview", help="validate, deduplicate and resolve outputs into a preview CSV")
    _add_dataset_args(merge, incoming=False)
    merge.add_argument("--input", action="append", default=[], help="nine-field contract file (repeatable)")
    merge.add_argument("--legacy-case-a", action="append", default=[], help="old eight-field Case A file")
    merge.add_argument("--legacy-case-b", action="append", default=[], metavar="FILE=SOURCE",
                       help="old seven-field Case B file and its source dataset name")
    merge.add_argument("--skip-case-a-handoffs", action="store_true",
                       help="skip legacy Case A rows that only route a tag to Case B")
    merge.add_argument("--decisions", help="JSON list of review decisions from an earlier run")
    merge.add_argument("--semantic-model", help="sentence model for semantic duplicate candidates")
    screening_defaults = ScreeningSettings()
    merge.add_argument("--screening-lexical", type=float, default=screening_defaults.lexical_threshold,
                       help="hierarchy lexical review threshold, 0-1 (default: %(default)s; uncalibrated)")
    merge.add_argument("--screening-semantic", type=float, default=screening_defaults.semantic_threshold,
                       help="hierarchy semantic review threshold, 0-1 (default: %(default)s; needs --semantic-model)")
    merge.add_argument("--out-dir", help="where to write the reports (default: merge_runs/<timestamp>)")
    merge.set_defaults(func=_cmd_merge_preview)

    bulk = subparsers.add_parser("bulk-decide", help="draft decisions for ambiguous tags by saturation similarity")
    _add_dataset_args(bulk)
    bulk.add_argument("--merge-run", required=True, help="merge-preview output folder (merge_runs/<timestamp>)")
    bulk.add_argument("--report", default="report.json", help="the saturation report Case A and Case B read")
    bulk.add_argument("--create-below", type=float,
                      help="create tags whose closest-match similarity (0-1) is below this")
    bulk.add_argument("--reuse-above", type=float,
                      help="reuse the closest baseline tag when similarity (0-1) is at or above this")
    bulk.add_argument("--decisions", help="existing decisions to keep and extend")
    bulk.add_argument("--output", default="decisions.json", help="where to write all decisions")
    bulk.set_defaults(func=_cmd_bulk_decide)

    integrate = subparsers.add_parser("integrate", help="turn a fully reviewed merge run into an enriched taxonomy")
    _add_dataset_args(integrate, incoming=False)
    integrate.add_argument("--merge-run", required=True, help="merge-preview output folder (merge_runs/<timestamp>)")
    integrate.add_argument("--out-dir", help="where to write the snapshot (default: integrations/<timestamp>)")
    integrate.set_defaults(func=_cmd_integrate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
