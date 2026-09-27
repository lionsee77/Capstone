from __future__ import annotations

from taxonomy_pipeline.datasets.sql_dump import SqlDumpSource, iter_created_tables, iter_insert_statements
from tests.conftest import FIXTURES


def test_parses_quoted_semicolon_and_escaped_quote_without_truncating():
    sql = """
    INSERT INTO `tags` (`id`, `name`, `note`) VALUES
    (1, 'Tax Advisory', 'Rate: 8%; applies broadly'),
    (2, 'O''Brien Law', NULL);
    """
    [(table, rows)] = list(iter_insert_statements(sql))
    assert table == "tags"
    assert rows[0] == {"id": 1, "name": "Tax Advisory", "note": "Rate: 8%; applies broadly"}
    assert rows[1] == {"id": 2, "name": "O'Brien Law", "note": None}


def test_parses_bare_unquoted_columns_generated_style():
    sql = "INSERT INTO sf_categories (id,name,service_id,status) VALUES\n(1,'Accountancy',1,'1');"
    [(table, rows)] = list(iter_insert_statements(sql))
    assert table == "sf_categories"
    assert rows == [{"id": 1, "name": "Accountancy", "service_id": 1, "status": "1"}]


def test_sql_dump_source_maps_tables_via_table_map():
    source = SqlDumpSource(
        name="reference",
        paths=[FIXTURES / "mini_reference.sql"],
        table_map={"categories": "sf_categories"},
    )
    dataset = source.load()
    assert dataset.name == "reference"
    assert [row["name"] for row in dataset.categories] == ["Accountancy", "Retail"]
    assert dataset.tags == []  # no sf_tags table in this fixture -> empty, not an error


def test_sql_dump_source_loads_all_tables_from_one_file():
    source = SqlDumpSource(name="original", paths=[FIXTURES / "mini_original.sql"])
    dataset = source.load()
    assert dataset.counts() == {
        "services": 0,
        "categories": 3,
        "specialities": 3,
        "tags": 3,
        "speciality_tags": 3,
    }


def test_escaped_backslash_followed_by_literal_n_is_not_corrupted_into_a_newline():
    # Raw dump bytes: \\n -- an escaped backslash (\\) immediately followed
    # by a literal 'n', which must decode to the two literal characters
    # '\' + 'n', not a real newline (regression for the old sequential
    # .replace() decoding, which mangled this case).
    sql = r"INSERT INTO `tags` (`id`, `name`) VALUES (1, 'C:\\new');"
    [(_, rows)] = list(iter_insert_statements(sql))
    assert rows[0]["name"] == "C:\\new"


def test_real_escaped_newline_still_decodes_to_a_newline_character():
    sql = r"INSERT INTO `tags` (`id`, `name`) VALUES (1, 'line one\nline two');"
    [(_, rows)] = list(iter_insert_statements(sql))
    assert rows[0]["name"] == "line one\nline two"


def test_sql_dump_source_merges_tables_split_across_files():
    # sql-files-nus keeps one table per file; loading them all should merge
    # into a single dataset, as if it were one dump.
    source = SqlDumpSource(
        name="original",
        paths=[FIXTURES / "mini_original.sql", FIXTURES / "mini_reference.sql"],
        table_map={"categories": "categories"},
    )
    dataset = source.load()
    # mini_reference.sql's rows land under sf_categories, which this
    # table_map doesn't point "categories" at, so they're simply not pulled
    # in - merging only combines rows for tables actually mapped.
    assert dataset.counts()["categories"] == 3


def test_ignores_insert_like_text_inside_a_quoted_field():
    sql = """
    INSERT INTO `tags` (`id`, `name`) VALUES
    (1, 'Example SQL: ; INSERT INTO fake_table (id, name) VALUES (99, ''not a row'')'),
    (2, 'Real tag');
    """

    statements = list(iter_insert_statements(sql))

    assert statements == [
        (
            "tags",
            [
                {"id": 1, "name": "Example SQL: ; INSERT INTO fake_table (id, name) VALUES (99, 'not a row')"},
                {"id": 2, "name": "Real tag"},
            ],
        )
    ]


def test_ignores_insert_like_text_inside_sql_comments():
    sql = """
    -- INSERT INTO fake_table (id, name) VALUES (1, 'not a row');
    /* INSERT INTO another_fake_table (id, name) VALUES (2, 'not a row'); */
    # INSERT INTO third_fake_table (id, name) VALUES (3, 'not a row');
    INSERT INTO `tags` (`id`, `name`) VALUES (1, 'Real tag');
    """

    statements = list(iter_insert_statements(sql))

    assert statements == [("tags", [{"id": 1, "name": "Real tag"}])]


def test_created_table_without_inserts_counts_as_present_but_empty(tmp_path):
    dump = tmp_path / "dump.sql"
    dump.write_text(
        "CREATE TABLE `services` (`id` int, `name` varchar(255));\n"
        "INSERT INTO `categories` (`id`, `name`, `service_id`) VALUES (1, 'Accounting', 999);\n"
    )
    dataset = SqlDumpSource(name="original", paths=[dump]).load()
    assert dataset.has_table("services") is True
    assert dataset.services == []
    assert dataset.has_table("categories") is True


def test_table_missing_from_the_dump_is_not_present(tmp_path):
    dump = tmp_path / "dump.sql"
    dump.write_text("INSERT INTO `categories` (`id`, `name`) VALUES (1, 'Accounting');\n")
    dataset = SqlDumpSource(name="original", paths=[dump]).load()
    assert dataset.has_table("services") is False
    assert dataset.services == []


def test_create_table_if_not_exists_like_counts_as_present_via_table_map(tmp_path):
    # The skillsfuture generator's style: sf_* tables cloned from the originals.
    dump = tmp_path / "dump.sql"
    dump.write_text("CREATE TABLE IF NOT EXISTS sf_categories LIKE categories;\n")
    dataset = SqlDumpSource(name="sf", paths=[dump], table_map={"categories": "sf_categories"}).load()
    assert dataset.has_table("categories") is True
    assert dataset.has_table("services") is False


def test_ignores_create_table_text_inside_quoted_fields_and_comments():
    sql = """
    -- CREATE TABLE services (id int);
    /* CREATE TABLE tags (id int); */
    INSERT INTO `categories` (`id`, `name`) VALUES (1, 'CREATE TABLE specialities (id int)');
    """
    assert list(iter_created_tables(sql)) == []
