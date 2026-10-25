"""Record every pipeline stage's output on the real data, and diff two recordings.

The bounded-context refactor (#104) must not change behaviour. `record` runs
one full classification round through the CLI, writing every stage's output
into one folder; `compare` diffs two such folders, ignoring what legitimately
differs between runs (run/event IDs, timestamps, the folder's own path). Record
on `dev` before a refactor step, record again after it, and compare:

    python experiments/golden_run.py record golden_runs/before
    python experiments/golden_run.py record golden_runs/after
    python experiments/golden_run.py compare golden_runs/before golden_runs/after

The round, in order, each stage reading the previous one's output:

1. `run`: score the original and SkillsFuture taxonomies (integrity, depth,
   breadth, saturation). Lexical only: no sentence model and no judge, so it
   is free and reproducible on any machine.
2. `case-a` and `case-b` on that saturation report. Case B runs its real code
   (batching, checkpoints, the SkillsFuture tag-to-skill mapping, prompt
   building, contract records) against `StubModel`, a deterministic stand-in
   for the LLM that answers from a hash of each tag ID. Every prompt's hash is
   recorded too, so a changed prompt shows up as a diff.
3. `merge-preview` of both outputs, `bulk-decide` (create below 0.7, reuse at or
   above 0.8: lexical scores all fall between the README's 0.6 and 0.9, which
   would draft nothing), and `merge-preview` again with the draft.
4. Every tag still pending is excluded, standing in for a reviewer, and
   `merge-preview` runs a last time, so `integrate` has a fully reviewed run.
5. `integrate`, `run` on the snapshot's `enriched.yaml`, and `check-comparable`
   between the two scorecards.

The scorecards' run IDs are pinned (`golden`, `golden-enriched`) so the
producers' run stamps and `check-comparable`'s output match between
recordings. Each command's exit code and output are
saved in `commands.json`.
"""
from __future__ import annotations

import argparse
import contextlib
import gzip
import hashlib
import io
import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PROJECT = Path(__file__).resolve().parents[1]
BACKEND = PROJECT.parent
GOLDEN_RUN_ID = "golden"

# Keys whose values differ on every run by design; `compare` drops them wherever they appear. Everything else,
# text included, must match exactly once the recording folder's own path is replaced.
VOLATILE_KEYS = {"run_id", "created_at", "finished_at", "recorded_at", "event_id", "submission_id", "generated_at"}

CONFIG = """\
# Golden-run scorecard: every scoring metric on both taxonomies, lexical only.
datasets:
  - name: original
    type: sql_dump
    paths:
      - {backend}/sql-files-nus/services.sql
      - {backend}/sql-files-nus/categories.sql
      - {backend}/sql-files-nus/specialities.sql
      - {backend}/sql-files-nus/tags.sql
      - {backend}/sql-files-nus/speciality_tags.sql
  - name: skillsfuture
    type: sql_dump
    paths: [{backend}/skillsfuture/skillsfuture_taxonomy.sql]
    table_map:
      categories: sf_categories
      specialities: sf_specialities
      tags: sf_tags
      speciality_tags: sf_speciality_tags
  - name: ssic
    type: ssic
    paths: [{backend}/reference_data/ssic/ssic2025-classification-structure.xlsx]
  - name: ssoc
    type: ssoc
    paths: [{backend}/reference_data/ssoc/ssoc2024-classification-structure.xlsx]

metrics:
  - id: integrity
    params: {{dataset: original, fuzzy_threshold: 85}}
  - id: integrity
    params: {{dataset: skillsfuture, fuzzy_threshold: 85}}
  - id: depth
    params: {{dataset: original}}
  - id: depth
    params: {{dataset: skillsfuture}}
  - id: breadth
    params: {{target: original, matching_method: lexical}}
  - id: breadth
    params: {{target: skillsfuture, matching_method: lexical}}
  - id: saturation
    params:
      baseline: original
      incoming: [skillsfuture]
      threshold: 90
      add_candidate_lexical_threshold: 60
      cross_level: true
      judge: null
"""


class StubModel:
    """Answers Case B's chat-completions calls deterministically, like `tests/test_classification_case_b.py`'s stub.

    It reads the batch and the allowed hierarchy back out of the prompt and picks, from a hash of each tag ID,
    one of: an existing placement (most tags), a new speciality, a new category, or no placement. That covers
    every path Case B's output takes through the merge stages.
    """

    def __init__(self) -> None:
        self.prompt_hashes: list[str] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    @staticmethod
    def _section(prompt: str, start: str, end: str) -> Any:
        return json.loads(prompt.split(start, 1)[1].split(end, 1)[0])

    async def _create(self, *, model: str, messages: list[dict[str, str]], temperature: float) -> SimpleNamespace:
        prompt = messages[0]["content"]
        self.prompt_hashes.append(hashlib.sha256(prompt.encode("utf-8")).hexdigest())
        services = self._section(prompt, "Allowed Ankit services:\n", "\nAnkit categories")
        categories = self._section(prompt, "(each includes its parent service):\n", "\nAnkit specialities")
        specialities = self._section(prompt, "(each includes its parent service and category):\n",
                                     "\nSkillsFuture skills:")
        batch = self._section(prompt, "\nSkillsFuture skills:\n", "\nReturn ONLY valid JSON")
        proposals = [self._propose(item, services, categories, specialities) for item in batch]
        content = json.dumps({"proposals": proposals})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    @staticmethod
    def _propose(
        item: dict[str, Any], services: list[str], categories: list[dict[str, str]],
        specialities: list[dict[str, str]],
    ) -> dict[str, Any]:
        h = int(hashlib.sha256(str(item["tag_id"]).encode("utf-8")).hexdigest(), 16)
        title = str(item.get("title") or item["tag_id"])
        base = {"tag": title, "tag_id": item["tag_id"], "confidence": round(0.3 + (h % 70) / 100, 2)}
        bucket = h % 20
        if bucket < 14:
            spec = specialities[h % len(specialities)]
            return {**base, "action": "assign_existing", "service": spec["service"], "category": spec["category"],
                    "speciality": spec["name"], "reason": "Stub: closest existing placement."}
        if bucket < 17:
            category = categories[h % len(categories)]
            return {**base, "action": "propose_new_speciality", "service": category["service"],
                    "category": category["name"], "speciality": f"{title} practice",
                    "reason": "Stub: new speciality under an existing category."}
        if bucket < 19:
            return {**base, "action": "propose_new_category", "service": services[h % len(services)],
                    "category": f"{title} studies", "speciality": title, "reason": "Stub: new category."}
        return {**base, "action": "assign_existing", "service": services[0], "category": "", "speciality": "",
                "reason": "Stub: no suitable placement."}


@contextlib.contextmanager
def _working_directory(path: Path) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _cli(log: list[dict[str, Any]], stage: str, *argv: str) -> int:
    """Run one CLI command in-process, recording its exit code and output."""
    from taxonomy_pipeline.cli import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    log.append({"stage": stage, "argv": list(argv), "exit": code, "stdout": out.getvalue(), "stderr": err.getvalue()})
    print(f"[{'OK' if code == 0 else 'EXIT ' + str(code)}] {stage}", file=sys.stderr)
    return code


def _require(code: int, stage: str, log: list[dict[str, Any]]) -> None:
    if code != 0:
        print(log[-1]["stderr"] or log[-1]["stdout"], file=sys.stderr)
        raise SystemExit(f"golden run stopped: {stage} exited {code}")


def _pin_run_id(path: Path, run_id: str) -> None:
    """Give a saved report a fixed run ID, so the stages that print or stamp it agree between recordings."""
    report = json.loads(path.read_text(encoding="utf-8"))
    report["run_id"] = run_id
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")


def _pending_exclusions(merge_run: Path) -> list[dict[str, Any]]:
    resolution = json.loads((merge_run / "resolution.json").read_text(encoding="utf-8"))
    return [
        {"members": [disposition["source"]], "action": "exclude",
         "reason": "Golden run: excluded in place of a reviewer so integrate has a fully reviewed run."}
        for disposition in resolution["dispositions"] if disposition["status"] == "pending"
    ]


def record(out: Path) -> None:
    from taxonomy_pipeline.classification import case_b

    if out.exists():
        raise SystemExit(f"{out} already exists; pick a new folder")
    out.mkdir(parents=True)
    out = out.resolve()
    log: list[dict[str, Any]] = []
    stub = StubModel()
    with _working_directory(out):
        Path("golden.yaml").write_text(CONFIG.format(backend=BACKEND), encoding="utf-8")
        _require(_cli(log, "run", "run", "--config", "golden.yaml", "--output", "report.json", "--no-save"),
                 "run", log)
        _pin_run_id(Path("report.json"), GOLDEN_RUN_ID)

        datasets = ["--config", "golden.yaml"]
        _require(_cli(log, "case-a", "case-a", *datasets, "--report", "report.json", "--semantic-model", "",
                      "--output", "case_a_output.json"), "case-a", log)
        original_client = case_b._openai_client
        case_b._openai_client = lambda debug: stub
        try:
            _require(_cli(log, "case-b", "case-b", *datasets, "--report", "report.json",
                          "--output", "case_b_output.json", "--batch-size", "20", "--model", "stub"), "case-b", log)
        finally:
            case_b._openai_client = original_client
        Path("case_b_prompts.json").write_text(json.dumps(stub.prompt_hashes, indent=2), encoding="utf-8")

        inputs = ["--input", "case_a_output.json", "--input", "case_b_output.json"]
        _cli(log, "merge-preview", "merge-preview", *datasets, *inputs, "--out-dir", "merge_runs/first")
        _require(_cli(log, "bulk-decide", "bulk-decide", *datasets, "--merge-run", "merge_runs/first",
                      "--report", "report.json", "--create-below", "0.7", "--reuse-above", "0.8",
                      "--output", "bulk_decisions.json"), "bulk-decide", log)
        _cli(log, "merge-preview (bulk decisions)", "merge-preview", *datasets, *inputs,
             "--decisions", "bulk_decisions.json", "--out-dir", "merge_runs/bulk")

        decisions = json.loads(Path("bulk_decisions.json").read_text(encoding="utf-8"))
        decisions += _pending_exclusions(Path("merge_runs/bulk"))
        Path("reviewed_decisions.json").write_text(json.dumps(decisions, indent=2), encoding="utf-8")
        _require(_cli(log, "merge-preview (reviewed)", "merge-preview", *datasets, *inputs,
                      "--decisions", "reviewed_decisions.json", "--out-dir", "merge_runs/reviewed"),
                 "merge-preview (reviewed)", log)

        _require(_cli(log, "integrate", "integrate", *datasets, "--merge-run", "merge_runs/reviewed",
                      "--out-dir", "integrations/golden"), "integrate", log)
        _require(_cli(log, "run (enriched)", "run", "--config", "integrations/golden/enriched.yaml",
                      "--output", "enriched_report.json", "--no-save"), "run (enriched)", log)
        _pin_run_id(Path("enriched_report.json"), f"{GOLDEN_RUN_ID}-enriched")
        _cli(log, "check-comparable", "check-comparable", "report.json", "enriched_report.json",
             "--before-subject", "original")
    (out / "commands.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
    print(f"recorded {out}", file=sys.stderr)


def _normalize(value: Any, root: str) -> Any:
    if isinstance(value, dict):
        return {key: _normalize(item, root) for key, item in value.items() if key not in VOLATILE_KEYS}
    if isinstance(value, list):
        return [_normalize(item, root) for item in value]
    if isinstance(value, str):
        return _normalize_text(value, root)
    return value


def _normalize_text(text: str, root: str) -> str:
    return text.replace(root, "<RUN>")


def _load(path: Path, root: str) -> Any:
    data = path.read_bytes()
    if path.suffix == ".gz":
        data, path = gzip.decompress(data), path.with_suffix("")
    if path.suffix == ".json":
        return _normalize(json.loads(data), root)
    if path.suffix == ".jsonl":
        return [_normalize(json.loads(line), root) for line in data.decode("utf-8").splitlines() if line.strip()]
    return _normalize_text(data.decode("utf-8"), root)


def _files(root: Path) -> dict[str, Path]:
    return {str(path.relative_to(root)): path for path in sorted(root.rglob("*")) if path.is_file()}


def _first_difference(before: Any, after: Any, where: str = "") -> str:
    if type(before) is not type(after):
        return f"{where or '<root>'}: {type(before).__name__} -> {type(after).__name__}"
    if isinstance(before, dict):
        for key in sorted(before.keys() | after.keys()):
            if key not in before or key not in after:
                return f"{where}.{key}: {'added' if key not in before else 'removed'}"
            if before[key] != after[key]:
                return _first_difference(before[key], after[key], f"{where}.{key}")
    if isinstance(before, list):
        if len(before) != len(after):
            return f"{where or '<root>'}: {len(before)} items -> {len(after)}"
        for index, (old, new) in enumerate(zip(before, after, strict=True)):
            if old != new:
                return _first_difference(old, new, f"{where}[{index}]")
    if isinstance(before, str):
        old, new = before.splitlines(), after.splitlines()
        for number, (a, b) in enumerate(zip(old, new, strict=False), start=1):
            if a != b:
                return f"{where or '<root>'} line {number}: {a[:150]!r} -> {b[:150]!r}"
        return f"{where or '<root>'}: {len(old)} lines -> {len(new)}"
    return f"{where or '<root>'}: {str(before)[:150]!r} -> {str(after)[:150]!r}"


def compare(before: Path, after: Path) -> int:
    old_files, new_files = _files(before), _files(after)
    problems = [f"only in {before}: {name}" for name in sorted(old_files.keys() - new_files.keys())]
    problems += [f"only in {after}: {name}" for name in sorted(new_files.keys() - old_files.keys())]
    for name in sorted(old_files.keys() & new_files.keys()):
        old = _load(old_files[name], str(before.resolve()))
        new = _load(new_files[name], str(after.resolve()))
        if old != new:
            problems.append(f"{name}: {_first_difference(old, new)}")
    for problem in problems:
        print(f"[DIFF] {problem}")
    shared = len(old_files.keys() & new_files.keys())
    print(f"{'identical' if not problems else f'{len(problems)} difference(s)'}: {shared} file(s) compared")
    return 0 if not problems else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    record_parser = commands.add_parser("record", help="run every stage and save its outputs")
    record_parser.add_argument("out", type=Path, help="new folder to record into, e.g. golden_runs/before")
    compare_parser = commands.add_parser("compare", help="diff two recordings, ignoring IDs, timestamps and paths")
    compare_parser.add_argument("before", type=Path)
    compare_parser.add_argument("after", type=Path)
    args = parser.parse_args(argv)
    if args.command == "record":
        record(args.out)
        return 0
    return compare(args.before, args.after)


if __name__ == "__main__":
    raise SystemExit(main())
