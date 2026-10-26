"""Integrate a reviewed merge run into an enriched taxonomy snapshot.

`merge-preview` stops at a reviewable preview whose new rows carry temporary
IDs (`new:category:<digest>` ...). This module is the step after review: it
re-verifies the merge run against the baseline through `build_preview` (the
same checks the preview ran), refuses while anything is still pending review,
gives every new row a real integer ID, and writes a snapshot directory:

    <out_dir>/
    ├── baseline/<file>.sql   byte-for-byte copies of the baseline dumps
    ├── additions.sql         INSERT-only: the new categories, specialities, tags and links
    ├── enriched.yaml         pipeline config loading baseline + additions as dataset `enriched`,
    │                         with the round's metrics re-pointed at it (see `_scoring_metrics`)
    └── manifest.json         provenance, counts, ID ranges, duplicate counts, temporary -> real IDs

Baseline rows are never rewritten, only copied, so "baseline unchanged" holds by
construction; `additions.sql` is also what would be applied to a database that
matches the baseline snapshot. Before the snapshot is moved into place it is
reloaded through `sql_dump` and checked: every row must round-trip exactly, and
integrity must report no new orphans or dead ends.

Nothing here touches a live database.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from ..config import DatasetConfig, PipelineConfig
from ..matching.names import collation_key
from ..quality.integrity import IntegrityMetric
from ..quality.registry import METRICS
from ..taxonomy.base import CONCEPTS, TaxonomyDataset
from ..taxonomy.sql_dump import SqlDumpSource
from .models import MergePreview, ResolutionReport
from .preview import build_preview
from .resolution import taxonomy_snapshot_id

ENRICHED_DATASET = "enriched"
# Matches the baseline: 104 of 110 categories and every speciality are active ('1').
NEW_ROW_STATUS = "1"
# Columns written for new rows. Everything else in the client schema is nullable
# or has a database default (created_at, user_id, url, meta_*).
COLUMNS = {
    "categories": ("id", "name", "service_id", "status"),
    "specialities": ("id", "name", "category_id", "status"),
    "tags": ("id", "name"),
    "speciality_tags": ("id", "speciality_id", "tag_id"),
}
# Parents before children, so the file applies cleanly with foreign keys.
INSERT_ORDER = ("categories", "specialities", "tags", "speciality_tags")
# Integrity findings an integration must never add.
GUARDED_FINDINGS = (
    ("orphans", "categories_without_service"),
    ("orphans", "specialities_without_category"),
    ("orphans", "tags_unused_by_any_speciality"),
    ("dead_ends", "categories_with_zero_specialities"),
    ("dead_ends", "specialities_with_zero_tags"),
)
SAMPLE = 10


class IntegrationError(ValueError):
    """The merge run cannot be integrated; the message says why and what to do."""


@dataclass
class Integration:
    """What one integration wrote."""

    out_dir: Path
    manifest: dict[str, Any]

    @property
    def counts(self) -> dict[str, int]:
        return self.manifest["counts"]


def integrate(
    resolution: ResolutionReport,
    baseline: DatasetConfig,
    *,
    out_dir: str | Path,
    provenance: dict[str, Any] | None = None,
    scoring_config: PipelineConfig | None = None,
) -> Integration:
    """Write the enriched snapshot for a fully reviewed merge run.

    `baseline` must be the `sql_dump` dataset the merge run was built against;
    its files are copied, so the snapshot stays loadable if they later change.
    `provenance` (e.g. the merge run's `run.json`) is recorded in the manifest.
    `scoring_config` is the round's pipeline config: every metric it runs on the
    baseline is written into `enriched.yaml` for the snapshot instead, so both
    are scored the same way, and so is its `metric_definitions_version` pin if it
    has one. Without it, `enriched.yaml` runs integrity and depth.
    """
    if baseline.type != "sql_dump":
        raise IntegrationError(f"Baseline {baseline.name!r} must be a sql_dump dataset, not {baseline.type!r}")
    out_dir = Path(out_dir)
    if out_dir.exists():
        raise IntegrationError(f"{out_dir} already exists; choose a new --out-dir")

    source = SqlDumpSource(baseline.name, baseline.paths, baseline.table_map)
    taxonomy = source.load()
    try:
        preview = build_preview(resolution, taxonomy)
    except ValueError as exc:  # baseline snapshot changed since the merge run
        raise IntegrationError(f"{exc}. Rerun merge-preview against the current baseline.") from exc
    _check_ready(preview)

    additions, id_map = _assign_ids(preview.taxonomy, taxonomy)
    if not any(additions.values()):
        omitted = sum(resolution.evidence.omitted_counts.values())
        raise IntegrationError(
            f"Nothing to integrate: the reviewed merge run adds no rows ({omitted} input record(s) were omitted "
            "upstream; see the merge run's validation.json)."
        )
    _check_unique_keys(additions, taxonomy)
    table_names = {concept: source.table_map[concept] for concept in COLUMNS}

    staging = Path(tempfile.mkdtemp(prefix=f".{out_dir.name}.", dir=_ensure_parent(out_dir)))
    try:
        copies = _copy_baseline(source.paths, staging / "baseline")
        (staging / "additions.sql").write_text(
            _additions_sql(additions, table_names, resolution.baseline_snapshot_id), encoding="utf-8",
        )
        enriched, duplicates = _verify(staging, copies, source, taxonomy, additions)
        manifest = _manifest(
            resolution, baseline, copies, additions, id_map, enriched, provenance or {}, staging,
        )
        manifest["exact_duplicate_rows"] = duplicates
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        (staging / "enriched.yaml").write_text(
            _config_yaml(copies, staging, source.table_map, baseline.name, scoring_config), encoding="utf-8",
        )
        os.replace(staging, out_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return Integration(out_dir=out_dir, manifest=manifest)


def _check_ready(preview: MergePreview) -> None:
    evidence = preview.resolution.evidence if preview.resolution else None
    if evidence is not None and not evidence.input_complete:
        raise IntegrationError(
            "An input file of the merge run could not be read (see its validation.json); fix it and rerun "
            "merge-preview before integrating."
        )
    if preview.blocked:
        introduced = [f.message for f in preview.validation.findings if f.code.startswith("introduced.")]
        raise IntegrationError(
            "The merge run is blocked and cannot be integrated: " + "; ".join(introduced[:SAMPLE])
        )
    dispositions = preview.resolution.dispositions if preview.resolution else []
    pending = [d.source for d in dispositions if d.status == "pending"]
    if pending:
        sample = ", ".join(f"{s.source_dataset}:{s.tag_id}" for s in pending[:SAMPLE])
        more = f" and {len(pending) - SAMPLE} more" if len(pending) > SAMPLE else ""
        raise IntegrationError(
            f"{len(pending)} source tag(s) are still pending review ({sample}{more}). Record a decision for each "
            "(create, reuse, exclude or defer) and rerun merge-preview with --decisions before integrating."
        )


def _assign_ids(
    combined: TaxonomyDataset, baseline: TaxonomyDataset,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    """Give new rows real IDs after each table's baseline maximum.

    New rows are the ones `build_preview` added with temporary string IDs. The
    order is by parent and name, never by digest or input order, so a rerun on
    the same inputs yields identical IDs and the numbering is readable.
    """
    id_map: dict[str, int] = {}
    next_id = {table: _max_id(baseline, table) + 1 for table in COLUMNS}

    def new_rows(table: str) -> list[dict[str, Any]]:
        return [row for row in getattr(combined, table) if _is_temporary(row.get("id"))]

    def assign(table: str, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            id_map[row["id"]] = next_id[table]
            next_id[table] += 1

    def real(value: Any) -> Any:
        return id_map.get(value, value) if _is_temporary(value) else value

    def name_key(row: dict[str, Any]) -> tuple[str, str, str]:
        name = str(row.get("name"))
        return name.casefold(), name, str(row["id"])

    categories = sorted(new_rows("categories"), key=lambda r: (r["service_id"], *name_key(r)))
    assign("categories", categories)
    specialities = sorted(new_rows("specialities"), key=lambda r: (real(r["category_id"]), *name_key(r)))
    assign("specialities", specialities)
    tags = sorted(new_rows("tags"), key=name_key)
    assign("tags", tags)
    links = sorted(new_rows("speciality_tags"), key=lambda r: (real(r["speciality_id"]), real(r["tag_id"])))
    assign("speciality_tags", links)

    unresolved = [value for table in COLUMNS for row in new_rows(table) for value in row.values()
                  if _is_temporary(value) and value not in id_map]
    if unresolved:
        raise IntegrationError(f"New rows reference unknown temporary IDs: {sorted(set(unresolved))[:SAMPLE]}")

    additions = {
        "categories": [{"id": real(r["id"]), "name": r["name"], "service_id": real(r["service_id"]),
                        "status": NEW_ROW_STATUS} for r in categories],
        "specialities": [{"id": real(r["id"]), "name": r["name"], "category_id": real(r["category_id"]),
                          "status": NEW_ROW_STATUS} for r in specialities],
        "tags": [{"id": real(r["id"]), "name": r["name"]} for r in tags],
        "speciality_tags": [{"id": real(r["id"]), "speciality_id": real(r["speciality_id"]),
                             "tag_id": real(r["tag_id"])} for r in links],
    }
    return additions, id_map


def _check_unique_keys(additions: dict[str, list[dict[str, Any]]], baseline: TaxonomyDataset) -> None:
    """Refuse additions that the client schema's unique keys would reject.

    `tags.name` is UNIQUE under utf8mb4_general_ci, so "Python" and "python"
    collide; `speciality_tags` is UNIQUE on (speciality_id, tag_id). The dump
    parser doesn't enforce either, so check here rather than at apply time.
    """
    problems = []
    existing = {collation_key(row.get("name")): row.get("name") for row in baseline.tags}
    added: dict[str, dict[str, Any]] = {}
    for row in additions["tags"]:
        key = collation_key(row["name"])
        if key in existing:
            problems.append(f"new tag {row['name']!r} (ID {row['id']}) collides with existing tag {existing[key]!r}")
        elif key in added:
            other = added[key]
            problems.append(
                f"new tags {other['name']!r} (ID {other['id']}) and {row['name']!r} (ID {row['id']}) share a name"
            )
        added.setdefault(key, row)
    pairs = {(row.get("speciality_id"), row.get("tag_id")) for row in baseline.speciality_tags}
    for row in additions["speciality_tags"]:
        pair = (row["speciality_id"], row["tag_id"])
        if pair in pairs:
            problems.append(f"link speciality {pair[0]} -> tag {pair[1]} already exists")
        pairs.add(pair)
    if problems:
        raise IntegrationError(
            "The additions would violate the client schema's unique keys: " + "; ".join(problems[:SAMPLE])
            + ". The database holds one tag per name, shared by every speciality it is linked to: `reuse` the "
            "existing tag, put same-named sources in one `create` decision (approving each of their paths), or "
            "`exclude` one, then rerun merge-preview."
        )


def _is_temporary(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("new:")


def _max_id(taxonomy: TaxonomyDataset, table: str) -> int:
    ids = [row.get("id") for row in getattr(taxonomy, table)]
    invalid = [identifier for identifier in ids if type(identifier) is not int]
    if invalid:
        raise IntegrationError(f"Baseline {table} has non-integer IDs {invalid[:SAMPLE]}; cannot assign new IDs")
    return max(ids, default=0)


def _sql_literal(value: Any) -> str:
    """A MySQL literal that `sql_dump` decodes back to the same value.

    Quotes are doubled (valid in every SQL mode). Backslash escapes for
    backslash and control characters rely on the default mode, which
    `additions.sql` sets in its header like the client's own dumps do.
    """
    if value is None:
        return "NULL"
    if type(value) is int:
        return str(value)
    escaped = (
        str(value).replace("\\", "\\\\").replace("'", "''").replace("\0", "\\0")
        .replace("\n", "\\n").replace("\r", "\\r").replace("\x1a", "\\Z")
    )
    return f"'{escaped}'"


def _additions_sql(additions: dict[str, list[dict[str, Any]]], table_names: dict[str, str], snapshot: str) -> str:
    lines = [
        "-- Enriched taxonomy additions generated by `taxonomy-pipeline integrate`.",
        f"-- Baseline snapshot: {snapshot}",
        "-- INSERT-only: no baseline row is updated or deleted. IDs continue from the baseline",
        "-- snapshot's maximum, so apply this only to a database matching that snapshot.",
        "",
        "-- Same session settings as the client's phpMyAdmin dumps: clears NO_BACKSLASH_ESCAPES,",
        "-- which the string escaping below relies on.",
        'SET SQL_MODE = "NO_AUTO_VALUE_ON_ZERO";',
        "SET NAMES utf8mb4;",
        "START TRANSACTION;",
    ]
    for concept in INSERT_ORDER:
        rows = additions[concept]
        if not rows:
            continue
        columns = COLUMNS[concept]
        values = ",\n".join("(" + ", ".join(_sql_literal(row[c]) for c in columns) + ")" for row in rows)
        header = ", ".join(f"`{c}`" for c in columns)
        lines += ["", f"INSERT INTO `{table_names[concept]}` ({header}) VALUES", f"{values};"]
    lines += ["", "COMMIT;", ""]
    return "\n".join(lines)


def _ensure_parent(out_dir: Path) -> Path:
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    return out_dir.parent


def _copy_baseline(paths: list[Path], target: Path) -> list[Path]:
    target.mkdir(parents=True)
    names = [path.name for path in paths]
    if len(set(names)) != len(names):  # same file name from different folders
        names = [f"{index:02d}_{path.name}" for index, path in enumerate(paths, start=1)]
    copies = []
    for path, name in zip(paths, names, strict=True):
        copy = target / name
        shutil.copyfile(path, copy)
        copies.append(copy)
    return copies


def _verify(
    staging: Path,
    copies: list[Path],
    source: SqlDumpSource,
    baseline: TaxonomyDataset,
    additions: dict[str, list[dict[str, Any]]],
) -> tuple[TaxonomyDataset, dict[str, dict[str, int]]]:
    """Reload the snapshot exactly as a pipeline run will, and refuse anything but a clean superset.

    New orphans and dead ends block the integration. New exact-duplicate names
    don't (the baseline itself reuses speciality names across categories), but
    the before/after row counts are returned for the manifest so they can't
    pass unnoticed.
    """
    enriched = SqlDumpSource(
        ENRICHED_DATASET, [*copies, staging / "additions.sql"], source.table_map,
    ).load()
    for concept in CONCEPTS:
        expected = getattr(baseline, concept) + additions.get(concept, [])
        if getattr(enriched, concept) != expected:
            raise IntegrationError(f"Reloaded {concept} differ from baseline + additions; nothing was written")

    before = _integrity(baseline)
    after = _integrity(enriched)
    introduced = []
    for section, check in GUARDED_FINDINGS:
        new_ids = set(after[section][check] or []) - set(before[section][check] or [])
        if new_ids:
            introduced.append(f"{check}: {sorted(new_ids, key=str)[:SAMPLE]}")
    if after["orphans"]["dangling_speciality_tag_links"] > before["orphans"]["dangling_speciality_tag_links"]:
        introduced.append("dangling_speciality_tag_links increased")
    if introduced:
        raise IntegrationError("Integration would introduce integrity problems: " + "; ".join(introduced))
    duplicates = {
        table: {
            "baseline": sum(len(cluster["ids"]) for cluster in before["exact_duplicates"][table]),
            "enriched": sum(len(cluster["ids"]) for cluster in after["exact_duplicates"][table]),
        }
        for table in before["exact_duplicates"]
    }
    return enriched, duplicates


def _integrity(taxonomy: TaxonomyDataset) -> dict[str, Any]:
    result = IntegrityMetric().run({taxonomy.name: taxonomy}, {"dataset": taxonomy.name})
    if result.status != "ok":
        raise IntegrationError(f"Integrity check failed on {taxonomy.name}: {result.summary}")
    return result.details


def _manifest(
    resolution: ResolutionReport,
    baseline: DatasetConfig,
    copies: list[Path],
    additions: dict[str, list[dict[str, Any]]],
    id_map: dict[str, int],
    enriched: TaxonomyDataset,
    provenance: dict[str, Any],
    staging: Path,
) -> dict[str, Any]:
    decisions = json.dumps([d.model_dump(mode="json") for d in resolution.decisions], sort_keys=True)
    inputs = sorted({origin.input_name for tag in resolution.evidence.tags for origin in tag.origins})
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "baseline": {
            "dataset": baseline.name,
            "snapshot_id": resolution.baseline_snapshot_id,
            "files": [str(copy.relative_to(staging)) for copy in copies],
        },
        "enriched_snapshot_id": taxonomy_snapshot_id(enriched),
        "merge_run": provenance,
        "inputs": inputs,
        "decisions": {
            "count": len(resolution.decisions),
            "sha256": hashlib.sha256(decisions.encode("utf-8")).hexdigest(),
        },
        # Records validation set aside (invalid, unresolved, flagged for review) never reach the snapshot.
        "omitted_records": dict(sorted(resolution.evidence.omitted_counts.items())),
        "new_row_status": NEW_ROW_STATUS,
        "counts": {concept: len(rows) for concept, rows in additions.items()},
        "id_ranges": {
            concept: [rows[0]["id"], rows[-1]["id"]] if rows else None for concept, rows in additions.items()
        },
        "id_map": id_map,
    }


def _scoring_metrics(
    config: PipelineConfig, baseline: str,
) -> tuple[str, list[dict[str, Any]], list[DatasetConfig]]:
    """The snapshot's dataset name, the config's metric entries that score `baseline` re-pointed at it,
    and the other datasets those entries read (reference workbooks, incoming sources).

    An entry scores the baseline when its metric's `subject_param` resolves to the baseline's name;
    everything else in the entry (thresholds, models, references) is kept, so the baseline and the
    snapshot are measured the same way. Entries scoring other datasets are left out. Dependencies
    are read from the entries as written, and the snapshot is named so it can't collide with any of
    them (`enriched`, else `enriched_2`, ...): two datasets sharing a name would make one silently
    replace the other when the config loads.
    """
    by_name = {dataset.name: dataset for dataset in config.datasets}
    selected: list[tuple[str, dict[str, Any], str]] = []
    referenced: set[str] = set()
    for entry in config.metrics:
        metric = METRICS.get(entry.id)
        param = metric.subject_param if metric else None
        if param is None or metric.resolve_params(entry.params).get(param) != baseline:
            continue
        selected.append((entry.id, entry.params, param))
        for key, value in metric.resolve_params(entry.params).items():
            for name in value if isinstance(value, list) else [value]:
                if key != param and isinstance(name, str) and name in by_name and name != baseline:
                    referenced.add(name)
    snapshot, suffix = ENRICHED_DATASET, 1
    while snapshot in referenced:
        suffix += 1
        snapshot = f"{ENRICHED_DATASET}_{suffix}"
    metrics = [{"id": metric_id, "params": {**params, param: snapshot}} for metric_id, params, param in selected]
    return snapshot, metrics, [dataset for name, dataset in by_name.items() if name in referenced]  # config order


def _config_yaml(
    copies: list[Path], staging: Path, table_map: dict[str, str], baseline: str,
    scoring_config: PipelineConfig | None,
) -> str:
    snapshot, metrics, referenced = (
        _scoring_metrics(scoring_config, baseline) if scoring_config else (ENRICHED_DATASET, [], [])
    )
    dataset: dict[str, Any] = {
        "name": snapshot,
        "type": "sql_dump",
        "paths": [str(copy.relative_to(staging)) for copy in copies] + ["additions.sql"],
    }
    custom = {concept: table for concept, table in table_map.items() if concept != table}
    if custom:
        dataset["table_map"] = custom
    # Integrity and depth always run: they are what `integrate` itself checks.
    for metric_id in ("depth", "integrity"):
        if not any(entry["id"] == metric_id for entry in metrics):
            metrics.insert(0, {"id": metric_id, "params": {"dataset": snapshot}})
    config: dict[str, Any] = {}
    if scoring_config and scoring_config.metric_definitions_version is not None:
        # A pinned round config (configs/baseline_v<N>.yaml) pins its snapshot's scoring too.
        config["metric_definitions_version"] = scoring_config.metric_definitions_version
    config["datasets"] = [dataset] + [other.model_dump(mode="json", exclude_defaults=True) for other in referenced]
    config["metrics"] = metrics
    header = (
        "# Generated by `taxonomy-pipeline integrate`. The snapshot's paths are relative to this file;\n"
        "# reference and incoming datasets keep the absolute paths of the round's config.\n"
        "# Score it with: taxonomy-pipeline run --config enriched.yaml\n"
    )
    return header + yaml.safe_dump(config, sort_keys=False)
