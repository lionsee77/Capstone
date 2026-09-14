"""`python -m taxonomy_pipeline run --config path/to/config.yaml`

The CLI is a thin wrapper around `pipeline.run_pipeline` - the exact same
function the FastAPI `/runs` route calls - plus optional SQLite persistence
and a JSON report on stdout or a file.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import db
from .config import load_config
from .pipeline import run_pipeline


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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
