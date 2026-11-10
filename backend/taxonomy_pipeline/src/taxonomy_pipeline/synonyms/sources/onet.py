"""O*NET alternate occupation titles as synonym suggestions.

Download **Alternate Titles** from onetcenter.org in **Excel** format: each row has an
occupation `Title` and one `Alternate Title`. (The tab-delimited text download has no
`Title` column, only the O*NET-SOC code, so it can't be matched to concept names on its
own; it is refused with that explanation.) Only taxonomy concepts named like an O*NET
occupation get suggestions, which in practice means the specialities an O*NET round
added (`backend/onet/`). O*NET is CC BY 4.0; credit "O*NET".
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from ...taxonomy.base import TaxonomyDataset
from .base import Suggestion, SynonymSource
from .labels import read_table, suggest_from_labels


class OnetSource(SynonymSource):
    id = "onet"
    description = "O*NET alternate titles for occupations whose title is a concept name"
    needs_file = True

    def suggest(self, taxonomy: TaxonomyDataset, path: Path | None = None) -> list[Suggestion]:
        if path is None:
            raise ValueError("the onet source needs --file: O*NET's Alternate Titles file (Excel)")
        if Path(path).suffix.lower() != ".xlsx":
            raise ValueError("the onet source reads the Excel Alternate Titles download: the text download has no "
                             "Title column to match concept names with")
        alternates: dict[str, list[str]] = defaultdict(list)
        for row in read_table(path, ("Title", "Alternate Title")):
            alternates[row["Title"]].append(row["Alternate Title"])
        return suggest_from_labels(taxonomy, (
            (title, labels, f"O*NET alternate title of {title!r}") for title, labels in alternates.items()
        ))
