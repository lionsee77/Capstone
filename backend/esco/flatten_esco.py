"""Join ESCO's linked CSV export into the one flat table `taxonomy-pipeline import-source` reads.

Why a script: ESCO is published as separate files joined by concept URI, not
as one row per occupation-skill link the way O*NET's Software Skills file is.
`import-source` only reads one flat table, so
this script only joins. It doesn't choose rows. Each link's `relationType` goes
into the output as `relation_type`, and `esco_mapping.yaml` decides which links
to keep (`filters`). The mapping also decides which column is the category,
speciality and tag and which service they go under. So the source goes through
the same importer, ID scheme and checks as every other one.

Inputs, from ESCO's CSV download (classification, English) in `raw/`:

- `occupations_en.csv`: `conceptUri`, `preferredLabel`, `iscoGroup` (4-digit ISCO-08 unit group)
- `skills_en.csv`: `conceptUri`, `preferredLabel`; `description` is used when present
- `occupationSkillRelations_en.csv`: `occupationUri`, `skillUri`, and `relationType`
  when present (a copy without it needs `--assume-relation-type`)

Output (default `raw/esco_flat.csv`), one row per occupation-skill link:
`isco_group, occupation, skill, skill_description, relation_type`.

ESCO v1.2.1 repeats a few occupation and skill rows. A repeat is dropped when
the columns the join uses (listed above) match. If they differ, the script
stops, because the join would have to guess which row is right. Other columns
are not compared. A repeat that differs only in those is still dropped, and the
script counts it separately so it shows up in the summary.

Run from `backend/esco/`: `python flatten_esco.py` (`--help` for options).
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

OCCUPATIONS = "occupations_en.csv"
SKILLS = "skills_en.csv"
RELATIONS = "occupationSkillRelations_en.csv"
OUTPUT_COLUMNS = ("isco_group", "occupation", "skill", "skill_description", "relation_type")


@dataclass
class Concepts:
    """One file's concepts by URI, and the repeats dropped while reading it."""

    by_uri: dict[str, dict[str, str]]
    repeats: int = 0
    repeats_differing_elsewhere: int = 0


@dataclass
class FlattenResult:
    """The flat rows plus what the join dropped, so the caller can report it."""

    rows: list[dict[str, str]]
    occupations: Concepts
    skills: Concepts
    links_read: int = 0
    repeated_links: int = 0
    dangling_links: int = 0
    relation_type_column: bool = True
    notes: list[str] = field(default_factory=list)


def _records(path: Path, required: tuple[str, ...]) -> tuple[list[str], Iterator[tuple[int, dict[str, str]]]]:
    """A CSV's headers and its records, each with the file line it starts on.

    The line comes from `reader.line_num`, which counts physical lines, so it
    stays right after a quoted field that spans several lines.
    """
    handle = path.open(newline="", encoding="utf-8-sig")
    reader = csv.DictReader(handle)
    headers = list(reader.fieldnames or [])
    missing = [column for column in required if column not in headers]
    if missing:
        handle.close()
        raise ValueError(f"{path}: missing column(s) {missing}; it has {headers}")

    def rows() -> Iterator[tuple[int, dict[str, str]]]:
        with handle:
            end = reader.line_num  # the header's last line
            for row in reader:
                start, end = end + 1, reader.line_num
                yield start, row

    return headers, rows()


def read_concepts(path: Path, columns: tuple[str, ...], optional: tuple[str, ...] = ()) -> Concepts:
    """Concepts by `conceptUri`, keeping `columns` (required) and whichever `optional` columns exist, stripped.

    A URI seen again is dropped when the kept columns match, and raises naming
    both rows' lines when they don't. A repeat that matches on the kept columns
    but differs in another one is counted in `repeats_differing_elsewhere`.
    """
    headers, records = _records(path, ("conceptUri", *columns))
    kept_columns = [*columns, *(column for column in optional if column in headers)]
    concepts = Concepts(by_uri={})
    first: dict[str, tuple[int, dict[str, str]]] = {}
    for line, row in records:
        uri = (row.get("conceptUri") or "").strip()
        if not uri:
            raise ValueError(f"{path}:{line}: missing conceptUri")
        concept = {column: (row.get(column) or "").strip() for column in kept_columns}
        if uri in concepts.by_uri:
            first_line, first_row = first[uri]
            if concepts.by_uri[uri] != concept:
                raise ValueError(f"{path}:{line}: conceptUri {uri} repeats line {first_line} with different data")
            concepts.repeats += 1
            concepts.repeats_differing_elsewhere += first_row != row
            continue
        concepts.by_uri[uri] = concept
        first[uri] = (line, row)
    return concepts


def flatten(raw_dir: Path, assume_relation_type: str | None = None) -> FlattenResult:
    """Join the three ESCO files in `raw_dir` into flat rows sorted by ISCO group, occupation and skill.

    `assume_relation_type` fills `relation_type` for a relations file without
    a `relationType` column, such as a copy already cut down to essential
    links. It is refused for a file that has the column, so it can't
    silently overrule ESCO's own value.
    """
    occupations = read_concepts(raw_dir / OCCUPATIONS, ("preferredLabel", "iscoGroup"))
    skills = read_concepts(raw_dir / SKILLS, ("preferredLabel",), optional=("description",))
    for uri, occupation in occupations.by_uri.items():
        group = occupation["iscoGroup"]
        if len(group) != 4 or not group.isdigit():
            raise ValueError(f"{raw_dir / OCCUPATIONS}: occupation {uri} has malformed iscoGroup {group!r}")

    relations = raw_dir / RELATIONS
    headers, links = _records(relations, ("occupationUri", "skillUri"))
    result = FlattenResult(
        rows=[], occupations=occupations, skills=skills, relation_type_column="relationType" in headers,
    )
    if result.relation_type_column and assume_relation_type is not None:
        raise ValueError(f"{relations} has a relationType column; drop --assume-relation-type")
    if not result.relation_type_column:
        result.notes.append(
            f"{RELATIONS} has no relationType column; relation_type is "
            + (f"{assume_relation_type!r} on every row (--assume-relation-type)" if assume_relation_type
               else "blank, so a mapping that filters on it keeps no rows")
        )
    seen: dict[tuple[str, str], tuple[int, str]] = {}
    for line, row in links:
        result.links_read += 1
        pair = ((row.get("occupationUri") or "").strip(), (row.get("skillUri") or "").strip())
        relation_type = (row.get("relationType") or "").strip() if result.relation_type_column \
            else assume_relation_type or ""
        if pair in seen:
            first_line, first_type = seen[pair]
            if first_type != relation_type:
                raise ValueError(f"{relations}:{line}: link {pair[0]} -> {pair[1]} repeats line {first_line} "
                                 f"as {relation_type!r} instead of {first_type!r}")
            result.repeated_links += 1
            continue
        seen[pair] = (line, relation_type)
        occupation, skill = occupations.by_uri.get(pair[0]), skills.by_uri.get(pair[1])
        if occupation is None or skill is None:
            result.dangling_links += 1
            continue
        result.rows.append({
            "isco_group": occupation["iscoGroup"],
            "occupation": occupation["preferredLabel"],
            "skill": skill["preferredLabel"],
            "skill_description": skill.get("description", ""),
            "relation_type": relation_type,
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
        "--assume-relation-type", metavar="TYPE",
        help="relation_type for every link when the relations file has no relationType column "
             "(e.g. 'essential' for a copy already cut down to essential links)",
    )
    args = parser.parse_args(argv)
    try:
        result = flatten(args.raw, args.assume_relation_type)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not result.rows:
        print("error: no occupation-skill link survived the join", file=sys.stderr)
        return 1
    output = args.output or args.raw / "esco_flat.csv"
    write_rows(result.rows, output)

    print(f"wrote {len(result.rows)} occupation-skill rows to {output}")
    print(f"  links read: {result.links_read}; repeated: {result.repeated_links}; "
          f"dangling (unknown occupation or skill): {result.dangling_links}")
    for label, concepts in (("occupation", result.occupations), ("skill", result.skills)):
        print(f"  repeated {label} rows dropped: {concepts.repeats} "
              f"({concepts.repeats_differing_elsewhere} differing only in columns the join doesn't use)")
    for note in result.notes:
        print(f"  note: {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
