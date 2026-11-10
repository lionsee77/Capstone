"""British/American spelling variants of concept names: the seed set (#60).

`match_key` already folds '-isation'/'-ization' and '&'/'and', so those variants match
without a synonym and aren't suggested. What it doesn't fold are whole-word spellings
("colour"/"color", "centre"/"center") and the '-ise'/'-ize' verbs ("organise"/"organize"),
so "Brand Colour" and "Brand Color" are different names to every stage until a
reviewer approves a synonym.

Everything here is an allowlist, because a rule over-reaches: "-re -> -er" turns
"genre" into "gener", and an "-ise -> -ize" regex turns "advertising" into
"advertizing" and "paradise" into "paradize". So:

- `WORDS` maps British to American spellings word by word; `AMERICAN` is its reverse,
  written out explicitly and leaving out American words with other British meanings
  ("check" is rarely a "cheque", a software "program" is never a "programme").
- `ISE_STEMS` lists the stems whose '-ise' and '-ize' forms are the same word; only
  those are respelled, with their usual endings.
"""
from __future__ import annotations

import re
from pathlib import Path

from ...matching.names import match_key
from ...taxonomy.base import TaxonomyDataset
from ..models import LEVELS
from .base import Suggestion, SynonymSource

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
# American -> British. Not every pair reverses: these American words have other meanings in British
# English, so respelling them would change the sense.
NOT_REVERSIBLE = {"check", "program", "programs", "tire", "tires", "license", "licenses"}
AMERICAN = {american: british for british, american in WORDS.items() if american not in NOT_REVERSIBLE}
# Stems whose -ise and -ize spellings are the same word.
ISE_STEMS = {
    "apolog", "authori", "capitali", "categori", "central", "character", "commerciali", "computeri", "containeri",
    "critici", "custom", "decentrali", "digiti", "economi", "emphas", "familiar", "finali", "globali", "harmon",
    "hospitali", "industriali", "initiali", "itemi", "legali", "locali", "maximi", "memor", "minimi", "mobili",
    "moderni", "moneti", "normali", "operationali", "optimi", "organ", "parameteri", "personali", "prioriti",
    "publici", "randomi", "real", "recogn", "revolution", "saniti", "seriali", "special", "stabili", "standardi",
    "summari", "symbol", "synchroni", "synthes", "theori", "tokeni", "utili", "vectori", "virtuali", "visuali",
}
ISE_ENDINGS = ("e", "ed", "es", "ing", "er", "ers", "ation", "ations")
_WORD = re.compile(r"[A-Za-z]+")


def _ise_variant(word: str) -> str | None:
    for stem in ISE_STEMS:
        for ending in ISE_ENDINGS:
            if word == f"{stem}is{ending}":
                return f"{stem}iz{ending}"
            if word == f"{stem}iz{ending}":
                return f"{stem}is{ending}"
    return None


def _respell(word: str) -> str | None:
    lower = word.lower()
    other = WORDS.get(lower) or AMERICAN.get(lower) or _ise_variant(lower)
    if other is None:
        return None
    return other.capitalize() if word[:1].isupper() else other


def variants(name: str) -> list[str]:
    """The other spelling of `name`, with every listed word respelled, or none. (The source then
    drops a variant `match_key` already folds into the name.)"""
    respelled = _WORD.sub(lambda m: _respell(m[0]) or m[0], name)
    return [respelled] if respelled != name else []


class SpellingSource(SynonymSource):
    id = "spelling"
    description = "British/American spellings of concept names that matching doesn't already fold"

    def suggest(self, taxonomy: TaxonomyDataset, path: Path | None = None) -> list[Suggestion]:
        suggestions = []
        for level in LEVELS:
            for row in getattr(taxonomy, level):
                name = row.get("name")
                if not isinstance(name, str):
                    continue
                for term in variants(name):
                    if match_key(term) == match_key(name):
                        continue  # matching already treats these as the same name
                    suggestions.append(Suggestion(
                        concept_level=level, concept_id=str(row.get("id")), term=term,
                        evidence=f"spelling variant of {name!r}",
                    ))
        return suggestions
