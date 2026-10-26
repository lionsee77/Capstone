"""The name folds in `matching/names.py` answer different questions; these pin where they differ."""
from __future__ import annotations

import pytest

from taxonomy_pipeline.matching.names import collation_key, is_valid_name, match_key, normalize


@pytest.mark.parametrize(("left", "right"), [("Python", "python"), ("Café", "cafe"), ("SQL  ", "sql")])
def test_collation_key_treats_case_accents_and_trailing_spaces_as_equal_like_mysql(left: str, right: str) -> None:
    assert collation_key(left) == collation_key(right)


def test_collation_key_keeps_what_mysql_keeps_distinct() -> None:
    # Leading spaces and inner punctuation still differ under utf8mb4_general_ci.
    assert collation_key(" sql") != collation_key("sql")
    assert collation_key("C++") != collation_key("C")


def test_normalize_folds_text_but_not_concepts() -> None:
    assert normalize("  Data   Visualisation! ") == "data visualisation"
    assert normalize("Sales & Marketing") != normalize("Sales and Marketing")


def test_match_key_also_folds_and_ampersands_isation_and_plurals() -> None:
    assert match_key("Sales & Marketing") == match_key("sales and marketing")
    assert match_key("Data Visualisation") == match_key("data visualization")
    assert match_key("Accountants") == match_key("accountant")


@pytest.mark.parametrize(("name", "valid"), [("Payroll", True), ("placeholder", False), ("---", False), (None, False)])
def test_is_valid_name_rejects_placeholders_and_names_without_letters_or_digits(name: str | None, valid: bool) -> None:
    assert is_valid_name(name) is valid
