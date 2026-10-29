"""Protect the shared ID assignment used by SQL generation and Case B."""
from __future__ import annotations

from taxonomy_pipeline.datasets.skillsfuture import build_skill_tag_mapping


def test_mapping_sorts_ids_and_excludes_untitled_or_missing_ids() -> None:
    assert build_skill_tag_mapping({
        "SK-B": "Second skill", "SK-A": "First skill", "SK-0": "  ", "": "Missing ID",
    }) == {"SK-A": 3000, "SK-B": 3001}


def test_mapping_normalises_workbook_whitespace() -> None:
    assert build_skill_tag_mapping({" SK- A ": "A title", "SK-B": "Another"}) == {
        "SK- A": 3000, "SK-B": 3001,
    }


def test_empty_workbook_has_no_mapping() -> None:
    assert build_skill_tag_mapping({}) == {}
