"""`taxonomy-pipeline run|check-comparable|compare|serve|import-source|case-a|case-b|merge-preview|
review-proposals|bulk-decide|integrate`

A thin adapter over `app/`: each command parses its flags, calls the one use
case behind it (`app.scoring` for `run` and `check-comparable`, `app.comparison` for `compare`,
`app.sources` for `import-source`, `app.enrichment` for the classification commands) and turns the result into
printed text and an exit code. `run` calls the same `run_pipeline` the FastAPI
`/runs` route calls. Anything else a command does - loading, checks, writing
outputs - belongs in its use case, not here.
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

from .app import comparison as comparisons
from .app import enrichment as use_cases
from .app.changes import changes, sync_changes
from .app.scoring import check_runs, result_labels, run_config, write_report
from .app.sources import import_source
from .audit.models import ChangeQuery


def _cmd_run(args: argparse.Namespace) -> int:
    _, report = run_config(args.config, save=not args.no_save)

    if args.output:
        write_report(report, args.output)
        print(f"wrote report to {args.output}")
    else:
        print(report.model_dump_json(indent=2))

    for result, label in zip(report.results, result_labels(report.results), strict=True):
        print(f"[{result.status.upper()}] {label}: {result.summary}", file=sys.stderr)
    if report.error:
        print(f"[FAILED] {report.error}", file=sys.stderr)

    # "done" only means the run completed - it says nothing about whether
    # every metric in it succeeded, so a CI gate needs both checked.
    any_metric_errors = any(result.status == "error" for result in report.results)
    return 0 if report.status == "done" and not any_metric_errors else 1


def _cmd_check_comparable(args: argparse.Namespace) -> int:
    try:
        before, after, check = check_runs(
            args.before, args.after, before_subject=args.before_subject, after_subject=args.after_subject,
        )
    except (OSError, ValueError) as exc:
        print(f"[FAILED] check-comparable: {exc}", file=sys.stderr)
        return 1

    for issue in check.issues:
        print(f"[FLAGGED] {issue.message}")
    labels = {"before": result_labels(before.results), "after": result_labels(after.results)}
    for metric in check.metrics:
        label = (labels["before"][metric.before_index] if metric.before_index is not None
                 else labels["after"][metric.after_index])
        if not metric.issues:
            print(f"[OK] {label}")
        for issue in metric.issues:
            print(f"[FLAGGED] {label}: {issue.message}")
    runs = [
        f"{run_id[:8]} ({subject or 'no scored taxonomy'}, "
        + (f"definitions v{version})" if version is not None else "definitions unknown)")
        for run_id, subject, version in (
            (check.before_run_id, check.before_subject, check.before_definitions_version),
            (check.after_run_id, check.after_subject, check.after_definitions_version),
        )
    ]
    print(f"{'comparable' if check.comparable else 'NOT comparable'}: before {runs[0]}, after {runs[1]}")
    return 0 if check.comparable else 1


def _cmd_compare(args: argparse.Namespace) -> int:
    try:
        fmt = comparisons.output_format(args.output, args.format)
        comparison = comparisons.compare_runs(
            args.before, args.after, before_subject=args.before_subject, after_subject=args.after_subject,
        )
        if args.output:
            comparisons.write_comparison(comparison, args.output, fmt)
            print(f"wrote {fmt} comparison to {args.output}", file=sys.stderr)
        else:
            print(comparisons.render(comparison, fmt), end="")
    except (OSError, ValueError) as exc:
        print(f"[FAILED] compare: {exc}", file=sys.stderr)
        return 1
    # Like check-comparable, a comparison that isn't like for like fails, so a script can't miss it.
    if not comparison.comparable:
        print("[FLAGGED] NOT comparable: changes are only shown for results scored alike. Why:", file=sys.stderr)
        for reason in comparisons.not_comparable_reasons(comparison):
            print(f"  - {reason}", file=sys.stderr)
        return 1
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .env import load_env

    # The API reads its token and data roots from the environment; load backend/.env first so settings
    # written there apply. The --reload worker inherits this environment.
    load_env()
    uvicorn.run("taxonomy_pipeline.api.app:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def _cmd_import_source(args: argparse.Namespace) -> int:
    try:
        result = import_source(
            args.mapping, out_dir=args.out_dir, baseline_snapshot=args.baseline_snapshot,
            template_config=args.template, round_config=args.round_config,
        )
    except (OSError, ValueError) as exc:
        print(f"[FAILED] import-source: {exc}", file=sys.stderr)
        return 1
    for warning in result.warnings:
        print(f"[WARN] {warning}", file=sys.stderr)
    counts = result.counts
    print(
        f"[OK] {counts['categories']} categories, {counts['specialities']} specialities, {counts['tags']} tags, "
        f"{counts['speciality_tags']} links",
        file=sys.stderr,
    )
    print(f"wrote {result.sql_path} and {result.context_path}")
    if result.round_config:
        names = f"--config {result.round_config} --baseline round_baseline --incoming {result.name}"
        print(f"wrote {result.round_config}; next:")
        print(f"  taxonomy-pipeline run --config {result.round_config} --output judged_report_{result.name}.json")
        print(f"  taxonomy-pipeline case-a {names} --report judged_report_{result.name}.json "
              f"--output case_a_{result.name}.json")
        print(f"  taxonomy-pipeline case-b {names} --report judged_report_{result.name}.json "
              f"--provider tag_context --context {result.context_path} --output case_b_{result.name}.json")
    return 0


def _cmd_case_a(args: argparse.Namespace) -> int:
    try:
        records = use_cases.case_a(
            args.config, args.report, baseline=args.baseline, incoming=args.incoming, output=args.output,
            semantic_model=args.semantic_model,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"[FAILED] case-a: {exc}", file=sys.stderr)
        return 1
    resolved = sum(record["category"] is not None for record in records)
    print(f"wrote {args.output}: {len(records)} records ({resolved} resolved, {len(records) - resolved} unresolved)")
    return 0


def _cmd_case_b(args: argparse.Namespace) -> int:
    try:
        use_cases.case_b(
            args.config, args.report, baseline=args.baseline, incoming=args.incoming, output=args.output,
            provider=args.provider, context=args.context, checkpoint=args.checkpoint, batch_size=args.batch_size,
            model=args.model,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"[FAILED] case-b: {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_merge_preview(args: argparse.Namespace) -> int:
    try:
        run = use_cases.merge_preview(
            args.config, args.input, baseline=args.baseline, out_dir=args.out_dir, decisions=args.decisions,
            semantic_model=args.semantic_model, screening_lexical=args.screening_lexical,
            screening_semantic=args.screening_semantic, change_log=args.change_log, actor=args.actor,
        )
    except (OSError, ValueError, sqlite3.Error) as exc:
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


# Print the shared saved review view without altering decisions, reports or taxonomy data.
def _cmd_review_proposals(args: argparse.Namespace) -> int:
    try:
        view = use_cases.review_proposals(
            args.merge_run, proposal_id=args.proposal_id, level=args.level, offset=args.offset, limit=args.limit,
        )
    except (OSError, ValueError) as exc:
        print(f"[FAILED] review-proposals: {exc}", file=sys.stderr)
        return 1
    print(view.model_dump_json(indent=2))
    return 0


def _cmd_bulk_decide(args: argparse.Namespace) -> int:
    try:
        draft = use_cases.bulk_decide(
            args.config, args.merge_run, args.report, baseline=args.baseline, incoming=args.incoming,
            output=args.output, create_below=args.create_below, reuse_above=args.reuse_above,
            decisions=args.decisions,
        )
    except (OSError, ValueError) as exc:
        print(f"[FAILED] bulk-decide: {exc}", file=sys.stderr)
        return 1
    counts = draft.counts
    print(
        f"[OK] drafted {counts['create']} create and {counts['reuse']} reuse decision(s); "
        f"{counts['left_for_review']} ambiguous tag(s) left for review, "
        f"{counts['held_for_another_reason']} held for another reason, {counts['not_ambiguous']} other pending, "
        f"{counts['already_decided']} already decided",
        file=sys.stderr,
    )
    output = Path(args.output)
    print(f"wrote {output} ({len(draft.decisions)} decisions); apply it with merge-preview --decisions {output}")
    return 0


def _cmd_integrate(args: argparse.Namespace) -> int:
    try:
        outcome = use_cases.integrate(
            args.config, args.merge_run, baseline=args.baseline, out_dir=args.out_dir, actor=args.actor,
        )
    except (OSError, ValueError) as exc:
        print(f"[FAILED] integrate: {exc}", file=sys.stderr)
        return 1
    for warning in outcome.warnings:
        print(f"[WARN] {warning}", file=sys.stderr)
    result = outcome.integration
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


# Print filtered audit history through the same use case as GET /changes.
def _cmd_changes(args: argparse.Namespace) -> int:
    try:
        query = ChangeQuery(**{key: getattr(args, key) for key in ChangeQuery.model_fields})
        print(changes(query).model_dump_json(indent=2))
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"[FAILED] changes: {exc}", file=sys.stderr)
        return 1
    return 0


# Retry delivery from saved audit events without changing taxonomy artifacts.
def _cmd_sync_changes(args: argparse.Namespace) -> int:
    try:
        count = sync_changes(args.events)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"[FAILED] sync-changes: {exc}", file=sys.stderr)
        return 1
    print(f"recorded {count} new audit events")
    return 0


# Configure the CLI adapters and their shared use-case inputs.
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="taxonomy_pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="run a pipeline config once")
    run_parser.add_argument("--config", required=True, help="path to a YAML pipeline config")
    run_parser.add_argument("--output", help="write the JSON report here instead of stdout (gzipped if it ends in .gz)")
    run_parser.add_argument("--no-save", action="store_true", help="don't persist this run to SQLite")
    run_parser.set_defaults(func=_cmd_run)

    check = subparsers.add_parser(
        "check-comparable", help="check two runs were scored with the same metric definitions and settings",
    )
    report_source = "report file (.json or .json.gz) or run ID from run history"
    check.add_argument("before", help=f"the earlier run, e.g. the baseline of record: {report_source}")
    check.add_argument("after", help=f"the later run, e.g. the enriched snapshot's: {report_source}")
    check.add_argument("--before-subject", help="taxonomy the before run scores (needed if it scores several)")
    check.add_argument("--after-subject", help="taxonomy the after run scores (needed if it scores several)")
    check.set_defaults(func=_cmd_check_comparable)

    compare = subparsers.add_parser(
        "compare", help="compare two runs metric by metric: values, changes and improved/deteriorated verdicts",
    )
    compare.add_argument("before", help=f"the earlier run, e.g. the baseline of record: {report_source}")
    compare.add_argument("after", help=f"the later run, e.g. the enriched snapshot's: {report_source}")
    compare.add_argument("--before-subject", help="taxonomy the before run scores (needed if it scores several)")
    compare.add_argument("--after-subject", help="taxonomy the after run scores (needed if it scores several)")
    compare.add_argument("--output", help="write the comparison here instead of stdout")
    compare.add_argument("--format", choices=comparisons.FORMATS,
                         help="markdown, csv or json (default: from --output's suffix, else markdown)")
    compare.set_defaults(func=_cmd_compare)

    serve_parser = subparsers.add_parser("serve", help="run the FastAPI app")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.add_argument("--reload", action="store_true")
    serve_parser.set_defaults(func=_cmd_serve)

    source = subparsers.add_parser("import-source", help="turn a flat CSV/xlsx source into a taxonomy SQL dump")
    source.add_argument("--mapping", required=True, help="source mapping YAML: file, columns, service, filters")
    source.add_argument("--out-dir", help="where to write <name>_taxonomy.sql and <name>_tag_context.json "
                        "(default: the mapping's folder)")
    source.add_argument("--baseline-snapshot",
                        help="previous round's integrate snapshot (integrations/<name>); also writes a round config")
    source.add_argument("--round-config", help="round config to write (default: configs/<snapshot>_vs_<name>.yaml)")
    source.add_argument("--template", help="round config whose metrics to copy "
                        "(default: configs/original_vs_skillsfuture.yaml)")
    source.set_defaults(func=_cmd_import_source)

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
    case_b.add_argument("--provider", help="source provider adding each tag's context (default: the --incoming name)")
    case_b.add_argument("--context", "--unique", dest="context",
                        help="the provider's context file (default: its own, e.g. the unique-skills workbook)")
    case_b.add_argument("--output", default="case_b_output.json", help="JSON output; a CSV is written next to it")
    case_b.add_argument("--checkpoint", help="checkpoint file for resuming after a failed run")
    case_b.add_argument("--batch-size", type=int, default=5)
    case_b.add_argument("--model", help="OpenAI model name (default: $OPENAI_MODEL or gpt-4o-mini)")
    case_b.add_argument("--debug-response", action="store_true",
                        help="also log the endpoint, model and up to 4,000 characters per response")
    case_b.set_defaults(func=_cmd_case_b)

    merge = subparsers.add_parser("merge-preview", help="validate, deduplicate and resolve outputs into a preview CSV")
    _add_dataset_args(merge, incoming=False)
    merge.add_argument("--input", action="append", default=[], help="nine-field contract file (repeatable)")
    merge.add_argument("--decisions", help="JSON list of tag and hierarchy ReviewDecision records from an earlier run")
    merge.add_argument("--semantic-model", help="sentence model for semantic duplicate candidates")
    screening_defaults = use_cases.SCREENING_DEFAULTS
    merge.add_argument("--screening-lexical", type=float, default=screening_defaults.lexical_threshold,
                       help="hierarchy lexical review threshold, 0-1 (default: %(default)s; uncalibrated)")
    merge.add_argument("--screening-semantic", type=float, default=screening_defaults.semantic_threshold,
                       help="hierarchy semantic review threshold, 0-1 (default: %(default)s; needs --semantic-model)")
    merge.add_argument("--out-dir", help="where to write the reports (default: merge_runs/<timestamp>)")
    merge.add_argument("--actor", default="unspecified", help="self-reported reviewer identity for new audit events")
    merge.add_argument("--change-log", help="review change log to append new decisions to, shared across runs "
                       "(default: <out-dir>/change_log.jsonl); decisions it already holds aren't logged again")
    merge.set_defaults(func=_cmd_merge_preview)

    review = subparsers.add_parser("review-proposals", help="read saved proposals, branches, matches and decisions")
    review.add_argument("--merge-run", required=True, help="merge-preview output folder")
    review.add_argument("--proposal-id", help="show a single screened hierarchy proposal")
    review.add_argument("--level", choices=["category", "speciality"])
    review.add_argument("--offset", type=int, default=0, help="skip this many proposals after filtering")
    review.add_argument("--limit", type=int, default=50, help="page size, 1-200 (default: 50)")
    review.set_defaults(func=_cmd_review_proposals)

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
    integrate.add_argument("--actor", default="unspecified", help="self-reported operator identity for audit events")
    integrate.set_defaults(func=_cmd_integrate)

    history = subparsers.add_parser("changes", help="read audit history without changing the taxonomy")
    for field in ChangeQuery.model_fields:
        if field not in {"limit", "offset"}:
            history.add_argument("--" + field.replace("_", "-"))
    history.add_argument("--offset", type=int, default=0)
    history.add_argument("--limit", type=int, default=50, help="page size, 1-200")
    history.set_defaults(func=_cmd_changes)
    sync = subparsers.add_parser("sync-changes", help="retry SQLite delivery of a saved audit_events.json file")
    sync.add_argument("--events", required=True)
    sync.set_defaults(func=_cmd_sync_changes)

    return parser


_log_handler: logging.Handler | None = None


def _show_library_logs(level: int) -> None:
    """Print the package's log records (e.g. Case B progress) to stdout as bare lines, as a command's own output.

    Bound to the current `sys.stdout` on every call, replacing the previous handler, so a caller
    that redirects stdout around `main` (tests, the golden run) captures them too.
    """
    global _log_handler
    package = logging.getLogger("taxonomy_pipeline")
    if _log_handler is not None:
        package.removeHandler(_log_handler)
    _log_handler = logging.StreamHandler(sys.stdout)
    _log_handler.setFormatter(logging.Formatter("%(message)s"))
    package.addHandler(_log_handler)
    package.setLevel(level)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _show_library_logs(logging.DEBUG if getattr(args, "debug_response", False) else logging.INFO)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
