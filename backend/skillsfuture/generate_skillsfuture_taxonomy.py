"""Generate a SkillsFuture taxonomy SQL seed file.

Hierarchy:
    existing services -> SkillsFuture sectors -> job roles -> Unique Skills tags

The source workbooks are read-only inputs. No database is contacted or changed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import tempfile
from pathlib import Path

from openpyxl import load_workbook


ROOT = Path(__file__).parent
DEFAULT_UNIQUE = ROOT / "jobsandskills-skillsfuture-unique-skills-list.xlsx"
DEFAULT_FRAMEWORK = ROOT / "jobsandskills-skillsfuture-skills-framework-dataset.xlsx"
DEFAULT_MAPPING = ROOT / "jobsandskills-skillsfuture-tsc-to-unique-skills-mapping.xlsx"


def rows(path: Path, sheet: str):
    ws = load_workbook(path, read_only=True, data_only=True)[sheet]
    iterator = ws.iter_rows(values_only=True)
    headers = [str(x).strip() if x is not None else "" for x in next(iterator)]
    for values in iterator:
        record = dict(zip(headers, values))
        if any(value not in (None, "") for value in values):
            yield record


def text(value) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def sql(value: str) -> str:
    return "'" + text(value).replace("\\", "\\\\").replace("'", "''") + "'"


def skill_records(path: Path) -> dict[str, dict[str, str]]:
    """Read eligible skills once for both SQL names and the Case B context file."""
    result: dict[str, dict[str, str]] = {}
    for row in rows(path, "Unique Skills List"):
        skill_id = text(row.get("skill_id"))
        title = text(row.get("skill_title"))
        if skill_id and title:
            result[skill_id] = {
                "title": title, "description": text(row.get("skill_description")), "type": text(row.get("skill_type")),
            }
    return result


def write_output_pair(outputs: dict[Path, str]) -> None:
    """Stage both files before replacing either, and roll back handled replacement failures.

    Filesystem renames cannot make two paths atomic together. Backups support
    rollback on ordinary errors; they are retained if rollback itself fails.
    """
    staged: dict[Path, Path] = {}
    backups: dict[Path, Path] = {}
    replaced: list[Path] = []
    preserve_backups = False

    def stage(path: Path, data: bytes) -> Path:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as stream:
            temporary = Path(stream.name)
            try:
                try:
                    mode = stat.S_IMODE(path.stat().st_mode)
                except FileNotFoundError:
                    mode = 0o644
                stream.write(data)
                stream.flush()
                os.fchmod(stream.fileno(), mode)
                os.fsync(stream.fileno())
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        return temporary

    try:
        for path, content in outputs.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            staged[path] = stage(path, content.encode("utf-8"))
            if path.exists():
                backups[path] = stage(path, path.read_bytes())
        try:
            for path, temporary in staged.items():
                os.replace(temporary, path)
                replaced.append(path)
        except OSError:
            try:
                for path in reversed(replaced):
                    if path in backups:
                        os.replace(backups[path], path)
                    else:
                        path.unlink(missing_ok=True)
            except OSError as exc:
                preserve_backups = True
                raise RuntimeError(f"Output rollback failed; recovery backups: {backups}") from exc
            raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)
        if not preserve_backups:
            for temporary in backups.values():
                temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--unique", type=Path, default=DEFAULT_UNIQUE)
    parser.add_argument("--framework", type=Path, default=DEFAULT_FRAMEWORK)
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    parser.add_argument("--output", type=Path, default=ROOT / "skillsfuture_taxonomy.sql")
    parser.add_argument(
        "--context-output", type=Path, help="tag context JSON (default: beside SQL, <source>_tag_context.json)",
    )
    args = parser.parse_args()
    context_path = args.context_output or args.output.with_name(
        args.output.stem.removesuffix("_taxonomy") + "_tag_context.json"
    )
    if context_path.resolve() == args.output.resolve():
        parser.error("--context-output must differ from the SQL --output path")

    skills = skill_records(args.unique)
    unique = {skill_id: skill["title"] for skill_id, skill in skills.items()}
    skill_title_to_id = {title.casefold(): skill_id for skill_id, title in unique.items()}

    roles = {}
    tsc_to_roles = {}
    for row in rows(args.framework, "Job Role_TSC_CCS"):
        sector, role, title = map(text, (row.get("Sector"), row.get("Job Role"), row.get("TSC_CCS Title")))
        code = text(row.get("TSC_CCS Code"))
        if sector and role:
            roles.setdefault((sector, role), set())
        if code and sector and role and title:
            tsc_to_roles.setdefault(code.casefold(), set()).add((sector, role, title))

    mapped = {}
    for row in rows(args.mapping, "data"):
        code = text(row.get("skills_framework_skill_code")).casefold()
        title = text(row.get("Unique skill_updated_skill_title"))
        if code and title and title.casefold() in skill_title_to_id:
            mapped.setdefault(code, set()).add(skill_title_to_id[title.casefold()])

    sectors = sorted({sector for sector, _ in roles})
    category_id = {sector: 1000 + i for i, sector in enumerate(sectors)}
    role_id = {key: 2000 + i for i, key in enumerate(sorted(roles))}
    tag_id = {skill_id: 3000 + i for i, skill_id in enumerate(sorted(unique))}

    links = set()
    for code, role_rows in tsc_to_roles.items():
        for sector, role, _ in role_rows:
            for skill_id in mapped.get(code, ()):
                links.add((role_id[(sector, role)], tag_id[skill_id]))

    lines = [
        "-- Generated by generate_skillsfuture_taxonomy.py; review before importing.",
        "-- Existing services are preserved. SkillsFuture sectors are under Industry (service_id=1).",
        "SET NAMES utf8mb4;", "START TRANSACTION;", "",
        "CREATE TABLE IF NOT EXISTS sf_categories LIKE categories;",
        "CREATE TABLE IF NOT EXISTS sf_specialities LIKE specialities;",
        "CREATE TABLE IF NOT EXISTS sf_tags LIKE tags;",
        "CREATE TABLE IF NOT EXISTS sf_speciality_tags LIKE speciality_tags;", "",
        "INSERT INTO sf_categories (id,name,service_id,status) VALUES",
    ]
    lines += [f"({category_id[s]}, {sql(s)}, 1, '1')" + ("," if i < len(sectors)-1 else ";") for i, s in enumerate(sectors)]
    lines += ["", "INSERT INTO sf_specialities (id,name,category_id,status) VALUES"]
    role_items = sorted(role_id.items(), key=lambda item: item[1])
    lines += [f"({rid}, {sql(role)}, {category_id[sector]}, '1')" + ("," if i < len(role_items)-1 else ";") for i, ((sector, role), rid) in enumerate(role_items)]
    lines += ["", "INSERT INTO sf_tags (id,name) VALUES"]
    tag_items = sorted(tag_id.items(), key=lambda item: item[1])
    lines += [f"({tid}, {sql(unique[sid])})" + ("," if i < len(tag_items)-1 else ";") for i, (sid, tid) in enumerate(tag_items)]
    lines += ["", "INSERT INTO sf_speciality_tags (id,speciality_id,tag_id) VALUES"]
    link_items = sorted(links)
    lines += [f"({4000+i}, {rid}, {tid})" + ("," if i < len(link_items)-1 else ";") for i, (rid, tid) in enumerate(link_items)]
    lines += ["", "COMMIT;", ""]
    payload = {
        "source": "skillsfuture", "display_name": "SkillsFuture",
        "tags": {str(tid): {"skill_id": sid, **skills[sid]} for sid, tid in tag_items},
    }
    write_output_pair({
        args.output: "\n".join(lines), context_path: json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    })
    print(f"Generated {args.output}")
    print(f"Generated {context_path}")
    print(f"categories={len(sectors)} specialities={len(role_id)} tags={len(tag_id)} links={len(links)}")


if __name__ == "__main__":
    main()
