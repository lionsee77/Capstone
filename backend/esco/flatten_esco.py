"""Join ESCO's linked CSV export into the one flat table `taxonomy-pipeline import-source` reads.

Why a script: ESCO is published as separate files joined by concept URI, not
as one row per occupation-skill link the way O*NET's Software Skills file is.
`import-source` only reads one flat table (#118 tracks generalising that), so
this script does the join and nothing else. Every taxonomy decision (which
column is the category, speciality and tag, the service, filters) stays in
`esco_mapping.yaml`, so the source goes through the same importer, ID scheme
and checks as every other one.

Inputs, from ESCO's CSV download (classification, English) in `raw/`:

- `occupations_en.csv`: `conceptUri`, `preferredLabel`, `iscoGroup` (4-digit ISCO-08 unit group)
- `skills_en.csv`: `conceptUri`, `preferredLabel`; `description` is used when present
- `occupationSkillRelations_en.csv`: `occupationUri`, `skillUri`; filtered on
  `relationType` when present (default: essential only)

Output (default `raw/esco_flat.csv`), one row per kept occupation-skill link:
`isco_group, occupation, skill, skill_description`.

ESCO v1.2.1 repeats a few occupation and skill rows verbatim. An exact repeat
is dropped; the same URI with different data stops the script, because then
the join would have to guess which row is right.

Run from `backend/esco/`: `python flatten_esco.py` (`--help` for options).
"""
from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path

OCCUPATIONS = "occupations_en.csv"
SKILLS = "skills_en.csv"
RELATIONS = "occupationSkillRelations_en.csv"
OUTPUT_COLUMNS = ("isco_group", "occupation", "skill", "skill_description")
DEFAULT_RELATION_TYPES = ("essential",)


@dataclass
class FlattenResult:
    """The flat rows plus what the join dropped, so the caller can report it."""

    rows: list[dict[str, str]]
    repeated_occupations: int = 0
    repeated_skills: int = 0
    links_read: int = 0
    links_filtered_out: int = 0
    repeated_links: int = 0
    dangling_links: int = 0
    relation_type_column: bool = True
    notes: list[str] = field(default_factory=list)


def _read(path: Path, required: tuple[str, ...]) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        headers = list(reader.fieldnames or [])
        missing = [column for column in required if column not in headers]
        if missing:
            raise ValueError(f"{path}: missing column(s) {missing}; it has {headers}")
        return headers, list(reader)


def read_concepts(
    path: Path, columns: tuple[str, ...], optional: tuple[str, ...] = (),
) -> tuple[dict[str, dict[str, str]], int]:
    """Concepts by `conceptUri`, keeping `columns` (required) and whichever `optional` columns exist, stripped.

    Returns them and how many exact repeats were dropped. A URI seen again with
    any different value raises, naming both rows' line numbers.
    """
    headers, rows = _read(path, ("conceptUri", *columns))
    kept_columns = [*columns, *(column for column in optional if column in headers)]
    concepts: dict[str, dict[str, str]] = {}
    first_line: dict[str, int] = {}
    repeats = 0
    for line, row in enumerate(rows, start=2):  # line 1 is the header
        uri = (row.get("conceptUri") or "").strip()
        if not uri:
            raise ValueError(f"{path}:{line}: missing conceptUri")
        concept = {column: (row.get(column) or "").strip() for column in kept_columns}
        if uri in concepts:
            if concepts[uri] != concept:
                raise ValueError(
                    f"{path}:{line}: conceptUri {uri} repeats line {first_line[uri]} with different data"
                )
            repeats += 1
            continue
        concepts[uri] = concept
        first_line[uri] = line
    return concepts, repeats


def flatten(raw_dir: Path, relation_types: tuple[str, ...] = DEFAULT_RELATION_TYPES) -> FlattenResult:
    """Join the three ESCO files in `raw_dir` into flat rows sorted by ISCO group, occupation and skill."""
    occupations, repeated_occupations = read_concepts(raw_dir / OCCUPATIONS, ("preferredLabel", "iscoGroup"))
    skills, repeated_skills = read_concepts(raw_dir / SKILLS, ("preferredLabel",), optional=("description",))
    for uri, occupation in occupations.items():
        group = occupation["iscoGroup"]
        if len(group) != 4 or not group.isdigit():
            raise ValueError(f"{raw_dir / OCCUPATIONS}: occupation {uri} has malformed iscoGroup {group!r}")

    headers, links = _read(raw_dir / RELATIONS, ("occupationUri", "skillUri"))
    result = FlattenResult(
        rows=[], repeated_occupations=repeated_occupations, repeated_skills=repeated_skills,
        links_read=len(links), relation_type_column="relationType" in headers,
    )
    if not result.relation_type_column:
        result.notes.append(
            f"{RELATIONS} has no relationType column; every link is kept (assumed already filtered)"
        )
    wanted = {relation_type.casefold() for relation_type in relation_types}
    seen: set[tuple[str, str]] = set()
    for row in links:
        if result.relation_type_column and (row.get("relationType") or "").strip().casefold() not in wanted:
            result.links_filtered_out += 1
            continue
        pair = ((row.get("occupationUri") or "").strip(), (row.get("skillUri") or "").strip())
        if pair in seen:
            result.repeated_links += 1
            continue
        seen.add(pair)
        occupation, skill = occupations.get(pair[0]), skills.get(pair[1])
        if occupation is None or skill is None:
            result.dangling_links += 1
            continue
        result.rows.append({
            "isco_group": occupation["iscoGroup"],
            "occupation": occupation["preferredLabel"],
            "skill": skill["preferredLabel"],
            "skill_description": skill.get("description", ""),
        })
    result.rows.sort(key=lambda row: (row["isco_group"], row["occupation"], row["skill"]))
    return result


def write_rows(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raw", type=Path, default=here / "raw", help="folder holding the ESCO CSVs (default: raw/)")
    parser.add_argument("--output", type=Path, help="flat CSV to write (default: <raw>/esco_flat.csv)")
    parser.add_argument(
        "--relation-type", action="append", dest="relation_types",
        help="relationType to keep; repeat for several (default: essential)",
    )
    args = parser.parse_args(argv)
    relation_types = tuple(args.relation_types or DEFAULT_RELATION_TYPES)
    try:
        result = flatten(args.raw, relation_types)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not result.rows:
        print("error: no occupation-skill link survived the join", file=sys.stderr)
        return 1
    output = args.output or args.raw / "esco_flat.csv"
    write_rows(result.rows, output)

    print(f"wrote {len(result.rows)} occupation-skill rows to {output}")
    print(f"  links read: {result.links_read}; filtered out by relationType: {result.links_filtered_out}; "
          f"repeated: {result.repeated_links}; dangling (unknown occupation or skill): {result.dangling_links}")
    print(f"  exact repeated rows dropped: {result.repeated_occupations} occupation(s), "
          f"{result.repeated_skills} skill(s)")
    for note in result.notes:
        print(f"  note: {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
