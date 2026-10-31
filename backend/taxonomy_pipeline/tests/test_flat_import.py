"""Flat import: one mapped CSV/xlsx table becomes a 4-table dump that `sql_dump` reads back unchanged."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
import yaml
from openpyxl import Workbook
from pydantic import ValidationError

from taxonomy_pipeline.taxonomy.flat_import import (
    COLUMNS,
    INSERT_CHUNK,
    SourceMapping,
    build_flat_taxonomy,
    dump_sql,
    dump_tag_context,
    load_mapping,
    read_rows,
    unlabelled_prefixes,
)
from taxonomy_pipeline.taxonomy.sql_dump import SqlDumpSource

HEADERS = ["Sector", "Role", "Skill", "Desc", "Keep"]
ROWS = [
    ["ICT", "Data Analyst", "SQL", "Query databases", "y"],
    ["ICT", "Data Analyst", "Python", "Write code", "y"],
    ["ICT", "Data Engineer", "SQL", "Query databases", "y"],
    ["Finance", "Auditor", "Excel", "", "n"],
]


def _csv(tmp_path: Path, rows: list[list[str]] = ROWS, name: str = "flat.csv") -> Path:
    path = tmp_path / name
    with path.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerows([HEADERS, *rows])
    return path


def _mapping(source: Path, **overrides) -> SourceMapping:
    fields = {
        "name": "demo", "display_name": "Demo", "source": str(source),
        "columns": {"category": "Sector", "speciality": "Role", "tag": "Skill", "tag_description": "Desc"},
        "service_id": 2, **overrides,
    }
    return SourceMapping.model_validate(fields)


def _build(mapping: SourceMapping):
    return build_flat_taxonomy(read_rows(mapping), mapping)


def test_builds_categories_specialities_tags_and_links_from_mapped_columns(tmp_path):
    taxonomy = _build(_mapping(_csv(tmp_path), filters={"Keep": "y"}))

    assert taxonomy.tables["categories"] == [{"id": 1000, "name": "ICT", "service_id": 2, "status": "1"}]
    assert [row["name"] for row in taxonomy.tables["specialities"]] == ["Data Analyst", "Data Engineer"]
    assert taxonomy.tables["tags"] == [{"id": 3000, "name": "Python"}, {"id": 3001, "name": "SQL"}]
    # SQL appears under two roles: one tag, two links.
    assert [(r["speciality_id"], r["tag_id"]) for r in taxonomy.tables["speciality_tags"]] == [
        (2000, 3000), (2000, 3001), (2001, 3001),
    ]
    assert taxonomy.tag_context == {"3000": {"description": "Write code"}, "3001": {"description": "Query databases"}}


def test_filters_keep_only_rows_matching_one_of_the_allowed_values(tmp_path):
    source = _csv(tmp_path)
    assert {row["Sector"] for row in read_rows(_mapping(source, filters={"Keep": ["n"]}))} == {"Finance"}
    assert len(read_rows(_mapping(source, filters={"Keep": ["y", "n"]}))) == 4


def test_reads_the_same_rows_from_an_xlsx_sheet(tmp_path):
    book = Workbook()
    sheet = book.active
    sheet.title = "Links"
    for row in [HEADERS, *ROWS]:
        sheet.append(row)
    path = tmp_path / "flat.xlsx"
    book.save(path)

    from_xlsx = _build(_mapping(path, sheet="Links"))
    from_csv = _build(_mapping(_csv(tmp_path)))
    assert from_xlsx.tables == from_csv.tables


def test_ids_follow_sorted_names_so_row_order_does_not_change_them(tmp_path):
    forward = _build(_mapping(_csv(tmp_path)))
    backward = _build(_mapping(_csv(tmp_path, list(reversed(ROWS)), name="reversed.csv")))
    assert forward.tables == backward.tables


def test_skips_and_counts_rows_missing_a_level_or_a_numeric_service(tmp_path):
    rows = [*ROWS, ["", "Orphan Role", "Skill", "", "y"], ["ICT", "Data Analyst", "", "", "y"]]
    taxonomy = _build(_mapping(_csv(tmp_path, rows)))
    assert taxonomy.skipped_rows == 2

    by_column = _build(_mapping(_csv(tmp_path, ROWS), service_id=None, service_column="Keep"))
    assert by_column.skipped_rows == len(ROWS) and not by_column.tables["categories"]


def test_a_missing_column_is_reported_with_the_headers_the_file_has(tmp_path):
    mapping = _mapping(_csv(tmp_path), columns={"category": "Sector", "speciality": "Job", "tag": "Skill"})
    with pytest.raises(ValueError, match=r"no column\(s\) \['Job'\]; its headers are \['Sector', 'Role'"):
        read_rows(mapping)


def test_mapping_needs_exactly_one_way_to_find_the_service(tmp_path):
    with pytest.raises(ValidationError, match="exactly one of service_id or service_column"):
        _mapping(tmp_path / "x.csv", service_id=None)
    with pytest.raises(ValidationError, match="exactly one of service_id or service_column"):
        _mapping(tmp_path / "x.csv", service_column="Keep")


def test_mapping_source_is_resolved_against_the_mapping_file(tmp_path):
    _csv(tmp_path)
    (tmp_path / "mapping.yaml").write_text(yaml.safe_dump({
        "name": "demo", "display_name": "Demo", "source": "flat.csv", "service_id": 1,
        "columns": {"category": "Sector", "speciality": "Role", "tag": "Skill"},
    }), encoding="utf-8")
    mapping = load_mapping(tmp_path / "mapping.yaml")
    assert Path(mapping.source) == (tmp_path / "flat.csv").resolve()
    assert mapping.table_map["tags"] == "demo_tags"


def test_dump_reads_back_unchanged_including_awkward_names(tmp_path):
    rows = [["Arts & Media", "O'Brien's role", "Back\\slash", "", "y"], ["Café", "Ünïcode", "Say \"hi\"", "", "y"]]
    mapping = _mapping(_csv(tmp_path, rows), table_prefix="x_")
    taxonomy = _build(mapping)
    path = tmp_path / "demo.sql"
    path.write_text(dump_sql(taxonomy, mapping), encoding="utf-8")

    reloaded = SqlDumpSource("demo", [path], mapping.table_map).load()
    for concept in COLUMNS:
        assert getattr(reloaded, concept) == taxonomy.tables[concept]


def test_large_tables_are_split_into_several_insert_statements(tmp_path):
    rows = [["ICT", "Role", f"Skill {n:05d}", "", "y"] for n in range(INSERT_CHUNK + 1)]
    mapping = _mapping(_csv(tmp_path, rows))
    taxonomy = _build(mapping)
    sql = dump_sql(taxonomy, mapping)
    assert sql.count("INSERT INTO `demo_tags`") == 2

    path = tmp_path / "demo.sql"
    path.write_text(sql, encoding="utf-8")
    assert len(SqlDumpSource("demo", [path], mapping.table_map).load().tags) == INSERT_CHUNK + 1


def test_tag_context_names_the_source_for_case_b(tmp_path):
    mapping = _mapping(_csv(tmp_path))
    context = json.loads(dump_tag_context(_build(mapping), mapping))
    assert context["display_name"] == "Demo" and context["source"] == "demo"
    # Excel (3000) has no description, so Case B gets only its name.
    assert context["tags"] == {"3001": {"description": "Write code"}, "3002": {"description": "Query databases"}}


ONET_HEADERS = ["O*NET-SOC Code", "Title", "Example", "Commodity Title"]


def _onet(tmp_path: Path, rows: list[list[str]], labels: str = "code,label\n15,Computer and Mathematical\n") -> Path:
    """An O*NET-shaped flat file, its SOC major-group labels and a mapping deriving the category from the code."""
    with (tmp_path / "tech.csv").open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerows([ONET_HEADERS, *rows])
    (tmp_path / "soc.csv").write_text(labels, encoding="utf-8")
    (tmp_path / "mapping.yaml").write_text(yaml.safe_dump({
        "name": "onet", "display_name": "O*NET", "source": "tech.csv", "service_id": 2,
        "columns": {
            "category": {"column": "O*NET-SOC Code", "prefix": 2, "labels": "soc.csv"},
            "speciality": "Title", "tag": "Example", "tag_description": "Commodity Title",
        },
    }), encoding="utf-8")
    return tmp_path / "mapping.yaml"


def test_derives_a_category_from_a_code_prefix_and_a_label_file(tmp_path):
    mapping = load_mapping(_onet(tmp_path, [
        ["15-2051.00", "Data Scientists", "Python", "Object or component oriented development software"],
        ["15-1252.00", "Software Developers", "Python", "Object or component oriented development software"],
    ]))
    taxonomy = build_flat_taxonomy(read_rows(mapping), mapping)

    assert taxonomy.tables["categories"] == [
        {"id": 1000, "name": "Computer and Mathematical", "service_id": 2, "status": "1"},
    ]
    assert [row["name"] for row in taxonomy.tables["specialities"]] == ["Data Scientists", "Software Developers"]
    assert taxonomy.tables["tags"] == [{"id": 3000, "name": "Python"}]
    assert len(taxonomy.tables["speciality_tags"]) == 2


def test_rows_whose_prefix_has_no_label_are_skipped_and_their_prefixes_reported(tmp_path):
    mapping = load_mapping(_onet(tmp_path, [
        ["15-2051.00", "Data Scientists", "Python", ""],
        ["99-0000.00", "Unknown", "Excel", ""],
    ]))
    rows = read_rows(mapping)
    assert build_flat_taxonomy(rows, mapping).skipped_rows == 1
    assert unlabelled_prefixes(rows, mapping) == ["99"]


def test_a_derived_columns_source_column_must_exist(tmp_path):
    path = _onet(tmp_path, [["15-2051.00", "Data Scientists", "Python", ""]])
    raw = yaml.safe_load(path.read_text())
    raw["columns"]["category"]["column"] = "SOC Code"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError, match=r"no column\(s\) \['SOC Code'\]; its headers are \['O\*NET-SOC Code'"):
        read_rows(load_mapping(path))


@pytest.mark.parametrize(("labels", "message"), [
    ("soc,name\n15,Computer\n", "needs 'code' and 'label' headers"),
    ("code,label\n15,Computer\n15,Maths\n", "lists code '15' more than once"),
])
def test_labels_file_needs_code_and_label_headers_and_unique_codes(tmp_path, labels, message):
    mapping = load_mapping(_onet(tmp_path, [["15-2051.00", "Data Scientists", "Python", ""]], labels=labels))
    with pytest.raises(ValueError, match=message):
        read_rows(mapping)


def test_labels_path_is_resolved_against_the_mapping_file(tmp_path):
    mapping = load_mapping(_onet(tmp_path, [["15-2051.00", "Data Scientists", "Python", ""]]))
    assert Path(mapping.columns.category.labels) == (tmp_path / "soc.csv").resolve()
