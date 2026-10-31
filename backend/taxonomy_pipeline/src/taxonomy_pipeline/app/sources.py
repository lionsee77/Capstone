"""Source use case: import a flat source as a taxonomy dump, and set up the round that compares it.

`import_source` is what `taxonomy-pipeline import-source` calls. It turns a
`SourceMapping` into `<name>_taxonomy.sql` and `<name>_tag_context.json`
(see `taxonomy/flat_import.py`), reloads the dump to prove it reads back
unchanged, and - given the previous round's `integrate` snapshot - writes the
next round's pipeline config: that snapshot as the baseline, the new source
as incoming, and a template round config's metrics re-pointed at both. Rounds
chain this way without anyone editing the original dumps or running SQL.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..config import load_config
from ..taxonomy.flat_import import (
    COLUMNS,
    build_flat_taxonomy,
    dump_sql,
    dump_tag_context,
    load_mapping,
    read_rows,
    unlabelled_prefixes,
)
from ..taxonomy.sql_dump import SqlDumpSource

# The dataset name a generated round config gives the previous round's snapshot.
ROUND_BASELINE = "round_baseline"
DEFAULT_TEMPLATE = Path(__file__).resolve().parents[3] / "configs" / "original_vs_skillsfuture.yaml"


@dataclass
class SourceImport:
    """What `import_source` wrote, plus anything a person should check before using it."""

    name: str
    sql_path: Path
    context_path: Path
    counts: dict[str, int]
    round_config: Path | None = None
    warnings: list[str] = field(default_factory=list)


def import_source(
    mapping_path: str | Path, *, out_dir: str | Path | None = None, baseline_snapshot: str | Path | None = None,
    template_config: str | Path | None = None, round_config: str | Path | None = None,
) -> SourceImport:
    """Write the source's dump and tag context into `out_dir` (default: next to the mapping).

    With `baseline_snapshot` (an `integrations/<name>/` folder), also write the
    round config to `round_config` (default `configs/<snapshot>_vs_<name>.yaml`),
    built from `template_config` (default `configs/original_vs_skillsfuture.yaml`).
    Refuses to overwrite an existing round config; the dump and context are
    regenerated freely, since they follow from the mapping alone.
    """
    mapping_path = Path(mapping_path)
    mapping = load_mapping(mapping_path)
    rows = read_rows(mapping)
    taxonomy = build_flat_taxonomy(rows, mapping)
    if not taxonomy.tables["speciality_tags"]:
        raise ValueError(f"{mapping.source}: no row has a category, speciality and tag after filtering")

    round_path = None
    if baseline_snapshot is not None:
        snapshot = Path(baseline_snapshot)
        round_path = Path(round_config or Path("configs") / f"{snapshot.name}_vs_{mapping.name}.yaml")
        if round_path.exists():
            raise ValueError(f"{round_path} already exists; delete it or pass another --round-config")
        _template_roles(mapping.name, template_config)  # refuse a clashing name before writing anything

    out = Path(out_dir) if out_dir is not None else mapping_path.parent
    out.mkdir(parents=True, exist_ok=True)
    sql_path = out / f"{mapping.name}_taxonomy.sql"
    context_path = out / f"{mapping.name}_tag_context.json"
    sql_path.write_text(dump_sql(taxonomy, mapping), encoding="utf-8")
    context_path.write_text(dump_tag_context(taxonomy, mapping), encoding="utf-8")

    reloaded = SqlDumpSource(mapping.name, [sql_path], mapping.table_map).load()
    for concept in COLUMNS:
        if getattr(reloaded, concept) != taxonomy.tables[concept]:
            raise ValueError(f"{sql_path}: {concept} did not read back unchanged; the dump is not usable")

    warnings = []
    if taxonomy.skipped_rows:
        warnings.append(f"skipped {taxonomy.skipped_rows} row(s) missing a category, speciality, tag or service")
    unlabelled = unlabelled_prefixes(rows, mapping)
    if unlabelled:
        warnings.append(f"no label for code prefix(es) {unlabelled}; their rows were skipped (check `prefix` and "
                        "the labels file)")
    if mapping.columns.tag_description is not None:
        undescribed = len(taxonomy.tables["tags"]) - len(taxonomy.tag_context)
        if undescribed:
            warnings.append(f"{undescribed} tag(s) have no description; Case B sees only their names")

    result = SourceImport(mapping.name, sql_path, context_path, taxonomy.counts, warnings=warnings)
    if round_path is not None:
        source_dataset = {"name": mapping.name, "type": "sql_dump", "paths": [str(sql_path.resolve())],
                          "table_map": mapping.table_map}
        write_round_config(Path(baseline_snapshot), source_dataset, round_path, template=template_config)
        result.round_config = round_path
    return result


def write_round_config(
    snapshot: Path, source_dataset: dict[str, Any], path: Path, *, template: str | Path | None = None,
) -> None:
    """Write a config comparing `source_dataset` against `snapshot`, scored like `template`'s round.

    The template's saturation metric names its baseline and incoming datasets;
    every metric param naming one of those is re-pointed (baseline ->
    `round_baseline`, incoming -> the new source), and a judge cache is renamed
    per source so verdicts from different rounds never mix. Other template
    datasets (ssic, ssoc) are kept. Paths are written relative to `path`.
    """
    template = Path(template) if template is not None else DEFAULT_TEMPLATE
    raw, rename = _template_roles(source_dataset["name"], template)

    snapshot_config = snapshot / "enriched.yaml"
    if not snapshot_config.exists():
        raise ValueError(f"{snapshot} is not an integrate snapshot (no enriched.yaml)")
    enriched = yaml.safe_load(snapshot_config.read_text(encoding="utf-8"))["datasets"][0]
    base_dir = path.parent.resolve()
    datasets = [
        {**enriched, "name": ROUND_BASELINE, "paths": _relative(enriched["paths"], snapshot, base_dir)},
        {**source_dataset, "paths": _relative(source_dataset["paths"], Path.cwd(), base_dir)},
    ]
    datasets += [
        {**dataset, "paths": _relative(dataset["paths"], template.parent, base_dir)}
        for dataset in raw.get("datasets", []) if dataset.get("name") not in rename
    ]
    metrics, seen = [], set()
    for metric in raw.get("metrics", []):
        entry = {"id": metric["id"], "params": _repoint(metric.get("params", {}), rename, source_dataset["name"])}
        key = json.dumps(entry, sort_keys=True)
        if key not in seen:
            seen.add(key)
            metrics.append(entry)

    config: dict[str, Any] = {}
    if raw.get("metric_definitions_version") is not None:
        config["metric_definitions_version"] = raw["metric_definitions_version"]
    config.update(datasets=datasets, metrics=metrics)
    header = (
        f"# Generated by `taxonomy-pipeline import-source`: {source_dataset['name']} against the {snapshot.name} "
        f"snapshot,\n# with the metrics of {template.name}. Paths are relative to this file.\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")
    load_config(path)  # fail now, not at `run`, if the result isn't a valid config


def _template_roles(source: str, template: str | Path | None) -> tuple[dict[str, Any], dict[str, str]]:
    """The template config and its dataset renames: saturation's baseline -> `round_baseline`, incoming -> `source`.

    Refuses a source named like a dataset the round config keeps (`round_baseline`,
    or a template dataset such as `ssic`): two datasets with one name would make
    the later one silently replace the other when the config loads.
    """
    template = Path(template) if template is not None else DEFAULT_TEMPLATE
    raw = yaml.safe_load(template.read_text(encoding="utf-8"))
    saturation = [m for m in raw.get("metrics", []) if m.get("id") == "saturation"]
    if len(saturation) != 1:
        raise ValueError(f"{template} needs exactly one saturation metric to tell its baseline from its incoming")
    params = saturation[0].get("params", {})
    incoming = params.get("incoming")
    rename = {params.get("baseline"): ROUND_BASELINE}
    rename.update({name: source for name in (incoming if isinstance(incoming, list) else [incoming])})
    kept = {ROUND_BASELINE} | {d.get("name") for d in raw.get("datasets", []) if d.get("name") not in rename}
    if source in kept:
        raise ValueError(f"source name {source!r} is already a dataset in the round config ({sorted(kept)}); "
                         "rename it in the mapping")
    return raw, rename


def _relative(paths: list[str], base: Path, target_dir: Path) -> list[str]:
    """Each path relative to `target_dir`, unless the two only share the filesystem root."""
    resolved = [(base / p).resolve() for p in paths]
    return [
        str(p) if os.path.commonpath([p, target_dir]) == p.anchor else os.path.relpath(p, target_dir)
        for p in resolved
    ]


def _repoint(value: Any, rename: dict[str, str], source: str) -> Any:
    if isinstance(value, dict):
        return {
            key: f"judge_cache_{source}.json" if key == "cache" and isinstance(item, str)
            else _repoint(item, rename, source)
            for key, item in value.items()
        }
    if isinstance(value, list):
        items = [_repoint(item, rename, source) for item in value]
        # Two template incomings renamed to the same source would otherwise repeat it.
        return list(dict.fromkeys(items)) if all(isinstance(item, str) for item in items) else items
    if isinstance(value, str):
        return rename.get(value, value)
    return value
