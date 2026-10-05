"""`taxonomy-pipeline run|serve|case-a|case-b|merge-preview`

Every command is a thin wrapper around one library function, so the CLI never
holds logic of its own: `run` calls `pipeline.run_pipeline` - the exact same
function the FastAPI `/runs` route calls - plus optional SQLite persistence and
a JSON report on stdout or a file. The classification commands call
`classification.case_a.run_case_a`, `classification.case_b.run_case_b` and
`classification.runner.run_merge_preview`.
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
        baseline = _datasets(args, args.baseline)[args.baseline]
        run = run_merge_preview(
            args.input, baseline, out_dir=out_dir,
            legacy_case_a=args.legacy_case_a, legacy_case_b=_legacy_case_b(args.legacy_case_b),
            skip_case_a_handoffs=args.skip_case_a_handoffs,
            decisions=load_decisions(args.decisions) if args.decisions else [],
            semantic_matcher=SemanticMatcher(args.semantic_model) if args.semantic_model else None,
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
    merge.add_argument("--out-dir", help="where to write the reports (default: merge_runs/<timestamp>)")
    merge.set_defaults(func=_cmd_merge_preview)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
