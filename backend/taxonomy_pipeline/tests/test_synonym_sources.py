"""Sources suggest synonyms; they never approve. Spelling needs no file, ESCO and O*NET read a download."""
from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook

from taxonomy_pipeline.synonyms.sources import SOURCES, get_source
from taxonomy_pipeline.synonyms.sources.spelling import variants
from taxonomy_pipeline.taxonomy.base import TaxonomyDataset

TAXONOMY = TaxonomyDataset(
    name="original", services=[{"id": 1, "name": "Industry"}],
    categories=[{"id": 1, "name": "Organisation & Strategy", "service_id": 1}],
    specialities=[{"id": 10, "name": "Data Scientists", "category_id": 1},
                  {"id": 11, "name": "Exercise Physiology", "category_id": 1}],
    tags=[{"id": 100, "name": "Machine learning"}, {"id": 101, "name": "Python"}, {"id": 102, "name": "python"}],
    speciality_tags=[],
)


def terms(suggestions) -> set[tuple[str, str, str]]:
    return {(s.concept_level, s.concept_id, s.term) for s in suggestions}


@pytest.mark.parametrize(("name", "expected"), [
    ("Organise Events", ["Organize Events"]),
    ("Customer Behavior", ["Customer Behaviour"]),
    ("Brand Colour & Design", ["Brand Color & Design"]),
    ("Research and Development", []),  # match_key already folds '&'/'and', so no synonym is needed
    ("Exercise Physiology", []),
    ("Genre Studies", []),
    ("Python", []),
])
def test_spelling_variants_respell_only_listed_words_and_stems(name: str, expected: list[str]) -> None:
    assert variants(name) == expected


@pytest.mark.parametrize("name", [
    "Advertising", "Fundraising", "Franchising", "Exercising", "Compromising", "Appraiser", "Paradise", "Cruise",
    "Downsizing", "Resizing", "Quality Check", "Background Check", "Computer Program", "Tire Fitting",
])
def test_words_outside_the_allowlist_or_with_another_british_sense_are_left_alone(name: str) -> None:
    assert variants(name) == []


def test_the_spelling_source_suggests_variants_matching_does_not_already_fold() -> None:
    # "Organisation & Strategy" -> "Organization & Strategy" is left out: match_key folds -isation.
    colourful = TAXONOMY.model_copy(update={"tags": [*TAXONOMY.tags, {"id": 103, "name": "Colour grading"}]})
    suggestions = get_source("spelling").suggest(colourful)
    assert terms(suggestions) == {("tags", "103", "Color grading")}
    assert suggestions[0].evidence == "spelling variant of 'Colour grading'"


def test_esco_suggests_alternative_labels_of_rows_whose_preferred_label_is_a_concept(tmp_path: Path) -> None:
    path = tmp_path / "skills_en.csv"
    path.write_text(
        'conceptType,preferredLabel,altLabels\n'
        'KnowledgeSkillCompetence,machine learning,"ML\nstatistical learning\nmachine learning"\n'
        'KnowledgeSkillCompetence,Python,"Python programming"\n'  # two of our tags are named python: skipped
        'KnowledgeSkillCompetence,welding,"arc welding"\n',
        encoding="utf-8",
    )
    suggestions = get_source("esco").suggest(TAXONOMY, path)
    assert terms(suggestions) == {("tags", "100", "ML"), ("tags", "100", "statistical learning")}
    assert suggestions[0].evidence == "ESCO alternative label of 'machine learning'"


def test_onet_suggests_alternate_titles_from_the_excel_download(tmp_path: Path) -> None:
    workbook = Workbook()
    workbook.active.append(["O*NET-SOC Code", "Title", "Alternate Title", "Short Title", "Source(s)"])
    workbook.active.append(["15-2051.00", "Data Scientists", "Data Mining Specialist", "", "08"])
    workbook.active.append(["15-2051.00", "Data Scientists", "Machine Learning Scientist", "", "08"])
    workbook.active.append(["47-2111.00", "Electricians", "Wireman", "", "08"])
    xlsx = tmp_path / "Alternate Titles.xlsx"
    workbook.save(xlsx)
    expected = {("specialities", "10", "Data Mining Specialist"), ("specialities", "10", "Machine Learning Scientist")}
    assert terms(get_source("onet").suggest(TAXONOMY, xlsx)) == expected


def test_onet_refuses_the_text_download_which_has_no_title_column(tmp_path: Path) -> None:
    text = tmp_path / "Alternate Titles.txt"  # O*NET's real text layout: no Title column
    text.write_text("O*NET-SOC Code\tAlternate Title\tShort Title\tSource(s)\n15-2051.00\tData Miner\t\t08\n",
                    encoding="utf-8")
    with pytest.raises(ValueError, match="Excel Alternate Titles download"):
        get_source("onet").suggest(TAXONOMY, text)


def test_tab_separated_files_are_read_without_quoting(tmp_path: Path) -> None:
    from taxonomy_pipeline.synonyms.sources.labels import read_table

    path = tmp_path / "titles.tsv"
    path.write_text('Title\tAlternate Title\nA\t"Quoted start\nB\tSecond\nC\tThird\n', encoding="utf-8")
    assert [row["Title"] for row in read_table(path, ("Title", "Alternate Title"))] == ["A", "B", "C"]


def test_a_corrupt_workbook_is_a_value_error_naming_the_file(tmp_path: Path) -> None:
    path = tmp_path / "skills.xlsx"
    path.write_text("not a zip", encoding="utf-8")
    with pytest.raises(ValueError, match="skills.xlsx couldn't be read"):
        get_source("esco").suggest(TAXONOMY, path)


def test_a_file_without_the_expected_columns_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "wrong.csv"
    path.write_text("label,other\nx,y\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"no column\(s\) \['preferredLabel', 'altLabels'\]"):
        get_source("esco").suggest(TAXONOMY, path)


def test_file_sources_need_a_file_and_unknown_sources_are_refused() -> None:
    assert sorted(SOURCES) == ["esco", "onet", "spelling"]
    for source in ("esco", "onet"):
        with pytest.raises(ValueError, match="needs --file"):
            get_source(source).suggest(TAXONOMY)
    with pytest.raises(ValueError, match="unknown synonym source 'wordnet'"):
        get_source("wordnet")
