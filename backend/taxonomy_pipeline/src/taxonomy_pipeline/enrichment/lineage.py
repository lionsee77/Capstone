"""Which integrated snapshot grew out of which: the rounds `integrate` wrote, read back as a lineage.

Each `integrate` writes `integrations/<name>/`: copies of the baseline it enriched
(`baseline/`, listed in `manifest.json`), the rows it added (`additions.sql`) and
provenance pointing at the reviewed merge run. A later round enriches an earlier
round's snapshot by copying those same files into its own `baseline/`, so a
round's parent is the snapshot whose files hold the same bytes as its baseline.

File contents are the link, not the manifest's snapshot IDs:
`taxonomy_snapshot_id` fingerprints the dataset's name too, and a round names its
baseline after its config (e.g. `round_baseline`) while `integrate` named the
same rows `enriched`, so the two IDs of one taxonomy differ.

`FileFingerprint` is also how the app layer recognises a run that scored one of
these snapshots: the run's dataset files hash to the same fingerprint, wherever
they were copied to.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# A taxonomy's files by content: the sorted SHA-256 digests of every file it loads from.
FileFingerprint = tuple[str, ...]

_DIGESTS: dict[tuple[str, int, int], str] = {}


def file_fingerprint(paths: list[str] | list[Path]) -> FileFingerprint | None:
    """The content fingerprint of a dataset's files, or None when one is missing or unreadable.

    Order-independent, since a config may list a dump's tables in any order. Digests are cached by
    path, size and modification time, so recognising runs re-reads only files that changed.
    """
    digests = []
    for path in paths:
        try:
            resolved = Path(path).resolve()
            stat = resolved.stat()
            key = (str(resolved), stat.st_size, stat.st_mtime_ns)
            if key not in _DIGESTS:
                digest = hashlib.sha256()
                with resolved.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1 << 20), b""):
                        digest.update(chunk)
                _DIGESTS[key] = digest.hexdigest()
            digests.append(_DIGESTS[key])
        except OSError:
            return None
    return tuple(sorted(digests))


@dataclass(frozen=True)
class IntegrationRound:
    """One `integrate` output: what it enriched, what it produced, and with which sources' tags."""

    name: str
    created_at: datetime | None
    baseline_dataset: str
    baseline: FileFingerprint
    snapshot: FileFingerprint
    # Incoming datasets the round's added tags came from, most tags first (e.g. ["skillsfuture"]).
    sources: list[str] = field(default_factory=list)
    # Rows the round added per table (`manifest.json` counts).
    added: dict[str, int] = field(default_factory=dict)


def read_rounds(integrations_root: Path, merge_root: Path) -> list[IntegrationRound]:
    """Every readable round under `integrations_root`, by directory name.

    A directory without a manifest, or whose manifest or files can't be read, is skipped with a
    warning rather than failing the lineage: one half-written round shouldn't hide the others.
    Sources are read from the round's merge run under `merge_root`; a merge run that's gone leaves
    them empty.
    """
    if not integrations_root.is_dir():
        return []
    rounds = []
    for directory in sorted(path for path in integrations_root.iterdir() if path.is_dir()):
        manifest_path = directory / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            rounds.append(_read_round(directory, json.loads(manifest_path.read_text(encoding="utf-8")), merge_root))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning("Skipping integration %s: %s", directory.name, exc)
    return rounds


def _read_round(directory: Path, manifest: dict[str, Any], merge_root: Path) -> IntegrationRound:
    baseline_files = [directory / name for name in manifest["baseline"]["files"]]
    baseline = file_fingerprint(baseline_files)
    snapshot = file_fingerprint([*baseline_files, directory / "additions.sql"])
    if baseline is None or snapshot is None:
        raise ValueError("a baseline file or additions.sql is missing")
    created = manifest.get("created_at")
    return IntegrationRound(
        name=directory.name,
        created_at=datetime.fromisoformat(created) if created else None,
        baseline_dataset=manifest["baseline"].get("dataset") or "baseline",
        baseline=baseline,
        snapshot=snapshot,
        sources=_sources(manifest.get("merge_run") or {}, merge_root),
        added={table: int(count) for table, count in (manifest.get("counts") or {}).items()},
    )


def _sources(provenance: dict[str, Any], merge_root: Path) -> list[str]:
    """The sources of the tags the round added, most added tags first.

    Read from the merge run's saved resolution: its `tags` not already in the baseline are the
    additions `integrate` wrote, and their `members` the source tags each was accepted from. Records
    omitted before review, or excluded or deferred in it, add nothing, so their sources don't count.
    The provenance path was written relative to wherever `merge-preview` ran, so only its last part
    is trusted, looked up under the configured merge root.
    """
    merge_run = provenance.get("merge_run")
    if not merge_run:
        return []
    resolution = merge_root / Path(str(merge_run)).name / "resolution.json"
    try:
        tags = json.loads(resolution.read_text(encoding="utf-8")).get("tags", [])
    except (OSError, ValueError):
        return []
    counts: Counter[str] = Counter()
    for tag in tags:
        if not isinstance(tag, dict) or tag.get("existing"):
            continue
        sources = {member.get("source_dataset") for member in tag.get("members", []) if isinstance(member, dict)}
        counts.update(source for source in sources if source)
    return [source for source, _ in counts.most_common()]
