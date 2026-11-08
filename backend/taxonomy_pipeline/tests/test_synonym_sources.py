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
    ("Organisation Design", ["Organization Design"]),
    ("Customer Behavior", ["Customer Behaviour"]),
    ("Research and Development", ["Research & Development"]),
    ("Data Analysis & Visualisation",
     ["Data Analysis & Visualization", "Data Analysis and Visualisation", "Data Analysis and Visualization"]),
    ("Exercise Physiology", []),  # "exercise" only has an -ise spelling
    ("Genre Studies", []),  # no "-re -> -er" rule: only listed words are respelled
    ("Python", []),
])
def test_spelling_variants_swap_listed_words_the_ise_family_and_ampersands(name: str, expected: list[str]) -> None:
    assert variants(name) == expected


def test_the_spelling_source_suggests_variants_of_every_concept_name() -> None:
    suggestions = get_source("spelling").suggest(TAXONOMY)
    assert terms(suggestions) == {
        ("categories", "1", "Organization & Strategy"), ("categories", "1", "Organisation and Strategy"),
        ("categories", "1", "Organization and Strategy"),
    }
    assert all("spelling variant of 'Organisation & Strategy'" == s.evidence for s in suggestions)


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


def test_onet_suggests_alternate_titles_from_an_excel_or_text_download(tmp_path: Path) -> None:
    workbook = Workbook()
    workbook.active.append(["O*NET-SOC Code", "Title", "Alternate Title", "Short Title", "Source(s)"])
    workbook.active.append(["15-2051.00", "Data Scientists", "Data Mining Specialist", "", "08"])
    workbook.active.append(["15-2051.00", "Data Scientists", "Machine Learning Scientist", "", "08"])
    workbook.active.append(["47-2111.00", "Electricians", "Wireman", "", "08"])
    xlsx = tmp_path / "Alternate Titles.xlsx"
    workbook.save(xlsx)
    text = tmp_path / "Alternate Titles.txt"
    text.write_text("O*NET-SOC Code\tTitle\tAlternate Title\n15-2051.00\tData Scientists\tData Mining Specialist\n",
                    encoding="utf-8")
    expected = {("specialities", "10", "Data Mining Specialist"), ("specialities", "10", "Machine Learning Scientist")}
    assert terms(get_source("onet").suggest(TAXONOMY, xlsx)) == expected
    assert terms(get_source("onet").suggest(TAXONOMY, text)) == {("specialities", "10", "Data Mining Specialist")}


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
