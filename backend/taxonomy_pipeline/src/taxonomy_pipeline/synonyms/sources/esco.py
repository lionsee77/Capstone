"""ESCO (the EU's skills and occupations classification): its alternative labels as synonym suggestions.

Download a language's skills or occupations CSV from the ESCO portal (e.g.
`skills_en.csv`, `occupations_en.csv`); each row has a `preferredLabel` and
`altLabels`, one label per line. ESCO is published under the EU's reuse licence
(CC BY 4.0); credit "ESCO" when redistributing derived data.
"""
from __future__ import annotations

from pathlib import Path

from ...taxonomy.base import TaxonomyDataset
from .base import Suggestion, SynonymSource
from .labels import read_table, suggest_from_labels


class EscoSource(SynonymSource):
    id = "esco"
    description = "ESCO alternative labels for skills/occupations whose preferred label is a concept name"
    needs_file = True

    def suggest(self, taxonomy: TaxonomyDataset, path: Path | None = None) -> list[Suggestion]:
        if path is None:
            raise ValueError("the esco source needs --file: an ESCO skills or occupations CSV")
        rows = read_table(path, ("preferredLabel", "altLabels"))
        return suggest_from_labels(taxonomy, (
            (label, row["altLabels"].splitlines(), f"ESCO alternative label of {label!r}")
            for row in rows for label in [row["preferredLabel"]]
        ))
