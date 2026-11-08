"""British/American spelling and '&'/'and' variants of concept names: the seed set (#60).

The matcher already folds some of these (`match_key`: '-isation', '&'), but lookups
and screening also compare plain `normalize`d names, where "Organisation Design" and
"Organization Design" differ. This source suggests the other spelling of every name
that has one, from a short curated word list plus the '-isation'/'-ise' family, so a
reviewer can approve the useful ones in bulk. Word-list entries only: a rule like
"-re -> -er" would turn "genre" into "gener".
"""
from __future__ import annotations

import re
from pathlib import Path

from ...taxonomy.base import TaxonomyDataset
from .base import LEVELS, Suggestion, SynonymSource

# British -> American, whole words, lower case.
WORDS = {
    "analyse": "analyze", "analysed": "analyzed", "analysing": "analyzing", "catalogue": "catalog",
    "centre": "center", "centres": "centers", "colour": "color", "colours": "colors", "defence": "defense",
    "behaviour": "behavior", "behavioural": "behavioral", "favour": "favor", "flavour": "flavor",
    "fulfil": "fulfill", "fulfilment": "fulfillment", "honour": "honor", "labour": "labor",
    "licence": "license", "licences": "licenses", "modelling": "modeling", "neighbour": "neighbor",
    "offence": "offense", "paediatric": "pediatric", "paediatrics": "pediatrics", "programme": "program",
    "programmes": "programs", "travelling": "traveling", "labelling": "labeling", "cancelling": "canceling",
    "aluminium": "aluminum", "anaesthesia": "anesthesia", "orthopaedic": "orthopedic", "tyre": "tire",
    "tyres": "tires", "cheque": "check", "grey": "gray", "jewellery": "jewelry", "mould": "mold",
    "enrolment": "enrollment", "counselling": "counseling", "signalling": "signaling",
}
AMERICAN = {american: british for british, american in WORDS.items()}
# -isation/-ise words become -ization/-ize, except words that only have an -ise spelling.
_ISE = re.compile(r"^(\w{3,})is(e|ed|es|ing|ation|ations|ational|er|ers)$")
_IZE = re.compile(r"^(\w{3,})iz(e|ed|es|ing|ation|ations|ational|er|ers)$")
ISE_ONLY = {
    "advise", "advised", "advises", "advising", "adviser", "advisers", "chastise", "comprise", "compromise",
    "concise", "despise", "devise", "disguise", "enterprise", "enterprises", "excise", "exercise", "exercises",
    "expertise", "franchise", "franchises", "improvise", "merchandise", "merchandising", "otherwise",
    "precise", "premise", "premises", "promise", "revise", "revised", "revising", "supervise", "supervised",
    "supervising", "supervisor", "surmise", "surprise", "televise", "wise",
}
_WORD = re.compile(r"[A-Za-z]+")


def _respell(word: str) -> str | None:
    lower = word.lower()
    other = WORDS.get(lower) or AMERICAN.get(lower)
    if other is None and lower not in ISE_ONLY:
        if match := _ISE.match(lower):
            other = f"{match[1]}iz{match[2]}"
        elif match := _IZE.match(lower):
            other = f"{match[1]}is{match[2]}"
    if other is None:
        return None
    return other.capitalize() if word[:1].isupper() else other


def variants(name: str) -> list[str]:
    """The other spellings of `name`: every respellable word swapped, then '&' and 'and' swapped."""
    found = []
    respelled = _WORD.sub(lambda m: _respell(m[0]) or m[0], name)
    if respelled != name:
        found.append(respelled)
    for text in [name, *found]:
        if " & " in text:
            found.append(text.replace(" & ", " and "))
        elif re.search(r" and ", text, flags=re.IGNORECASE):
            found.append(re.sub(r" and ", " & ", text, flags=re.IGNORECASE))
    return list(dict.fromkeys(found))


class SpellingSource(SynonymSource):
    id = "spelling"
    description = "British/American spellings and '&'/'and' variants of every concept name"

    def suggest(self, taxonomy: TaxonomyDataset, path: Path | None = None) -> list[Suggestion]:
        suggestions = []
        for level in LEVELS:
            for row in getattr(taxonomy, level):
                name = row.get("name")
                if not isinstance(name, str):
                    continue
                for term in variants(name):
                    suggestions.append(Suggestion(
                        concept_level=level, concept_id=str(row.get("id")), term=term,
                        evidence=f"spelling variant of {name!r}",
                    ))
        return suggestions
