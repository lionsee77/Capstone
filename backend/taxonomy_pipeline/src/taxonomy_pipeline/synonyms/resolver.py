"""What a term means in one taxonomy: its own concept names plus approved synonyms (#61, used by #60).

A term is compared after `normalize`, like every name lookup. Candidates are taken
in tiers, most specific first, and the first tier with any candidate decides:

1. synonyms scoped to the branch the lookup is made in (a speciality's scope before
   its category's), so a scoped synonym can give a word a local meaning;
2. concept names: a term that *is* a concept's name means that concept;
3. global synonyms.

If the deciding tier names more than one concept, the term is `ambiguous` and every
candidate is returned: the resolver never picks one. Two concepts sharing a name
(the baseline has such duplicates) are ambiguous in the same way.

`check_new` enforces the rules that keep global synonyms unambiguous: a global
synonym may not be another concept's name or another concept's global synonym, so a
word with several meanings has to be scoped. The index is pure data, built per call
from a taxonomy and the synonyms the caller loaded (stored `Synonym` rows, of which
only approved ones count, or `SynonymMapping`s from screening settings or an exported
synonym set); it never reads storage itself.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from ..matching.names import normalize
from ..taxonomy.base import TaxonomyDataset
from ..taxonomy.synonym_set import SynonymMapping
from .models import AUDIT_LEVELS, Resolution, ResolvedConcept, Synonym, SynonymCreate
from .store import SynonymError

LEVELS = ("categories", "specialities", "tags")
# Deeper scopes are more specific, so they're consulted first.
SCOPE_DEPTH = {"specialities": 0, "categories": 1}


class ConflictingSynonym(SynonymError):
    """The term already means another concept where the new synonym would apply."""


class InvalidScope(SynonymError):
    """The scope doesn't exist, or the concept doesn't sit inside it."""


class SynonymIndex:
    """Concept names and approved synonyms of one taxonomy, folded once for lookups."""

    def __init__(self, taxonomy: TaxonomyDataset, synonyms: Iterable[Synonym | SynonymMapping] = ()) -> None:
        self.taxonomy = taxonomy
        self.hierarchy = taxonomy.hierarchy()
        self.names: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
        self.concepts: dict[tuple[str, str], dict[str, Any]] = {}
        for level in LEVELS:
            for row in getattr(taxonomy, level):
                name = row.get("name")
                if isinstance(name, str) and normalize(name):
                    self.names[normalize(name)].append((level, str(row.get("id")), name))
                    self.concepts.setdefault((level, str(row.get("id"))), row)
        self.global_synonyms: dict[str, list[SynonymMapping]] = defaultdict(list)
        self.scoped_synonyms: dict[str, list[SynonymMapping]] = defaultdict(list)
        for synonym in synonyms:
            if isinstance(synonym, Synonym):
                if synonym.status != "approved":
                    continue
                synonym = synonym.mapping()
            target = self.scoped_synonyms if synonym.scope else self.global_synonyms
            target[synonym.normalized_term].append(synonym)

    def branches(self, level: str, concept_id: str) -> set[tuple[str, str]]:
        """The category and speciality branches a concept sits in, itself included when it is one."""
        row = self.concepts.get((level, concept_id))
        if row is None:
            return set()
        if level == "categories":
            return {("categories", concept_id)}
        if level == "specialities":
            category = self.hierarchy.category_of(row)
            return {("specialities", concept_id)} | ({("categories", str(category.get("id")))} if category else set())
        found: set[tuple[str, str]] = set()
        for path in self.hierarchy.tag_paths(row.get("id")):
            found.add(("specialities", str(path.speciality.get("id"))))
            if path.category is not None:
                found.add(("categories", str(path.category.get("id"))))
        return found

    def resolve(self, term: str, *, level: str | None = None, within: tuple[str, str] | None = None) -> Resolution:
        """What `term` means, optionally only at `level`, for a lookup made inside branch `within`."""
        key = normalize(term)
        wanted = (lambda candidate: candidate == level) if level else (lambda _: True)
        tiers: list[list[ResolvedConcept]] = []
        if within is not None:
            context = self.branches(*within)
            scoped = [s for s in self.scoped_synonyms.get(key, []) if s.scope in context and wanted(s.concept_level)]
            for depth in sorted(set(SCOPE_DEPTH.values())):
                tiers.append([_via_synonym(s) for s in scoped if SCOPE_DEPTH[s.scope_level] == depth])
        tiers.append([
            ResolvedConcept(concept_level=lvl, concept_id=cid, concept_name=name, via="name")
            for lvl, cid, name in self.names.get(key, []) if wanted(lvl)
        ])
        tiers.append([_via_synonym(s) for s in self.global_synonyms.get(key, []) if wanted(s.concept_level)])
        for matches in tiers:
            concepts = {(match.concept_level, match.concept_id) for match in matches}
            if concepts:
                return Resolution(term=term, status="resolved" if len(concepts) == 1 else "ambiguous", matches=matches)
        return Resolution(term=term, status="unknown")

    def check_new(self, request: SynonymCreate) -> None:
        """Refuse a synonym that would make a term ambiguous, or whose scope doesn't contain its concept.

        Global: the term may not already be another concept's name or global synonym.
        Scoped: the scope must be a branch the concept sits in (not the concept itself),
        and no other concept may have the same synonym in the same scope.
        """
        key, target = normalize(request.term), (request.concept_level, request.concept_id)
        if request.scope is not None:
            if request.scope not in self.concepts:
                raise InvalidScope(f"{self.taxonomy.name} has no {AUDIT_LEVELS[request.scope[0]]} "
                                   f"with ID {request.scope[1]} to scope to")
            if request.scope == target or request.scope not in self.branches(*target):
                raise InvalidScope(f"{AUDIT_LEVELS[target[0]]} {target[1]} doesn't sit inside "
                                   f"{AUDIT_LEVELS[request.scope[0]]} {request.scope[1]}")
            for other in self.scoped_synonyms.get(key, []):
                if other.scope == request.scope and (other.concept_level, other.concept_id) != target:
                    raise ConflictingSynonym(
                        f"{request.term!r} already means {other.concept_name!r} in that scope "
                        f"(synonym {other.synonym_id})"
                    )
            return
        for level, concept_id, name in self.names.get(key, []):
            if (level, concept_id) != target:
                raise ConflictingSynonym(
                    f"{request.term!r} is already the name of {AUDIT_LEVELS[level]} {concept_id} ({name!r}); "
                    "scope the synonym to a category or speciality instead"
                )
        for other in self.global_synonyms.get(key, []):
            if (other.concept_level, other.concept_id) != target:
                raise ConflictingSynonym(
                    f"{request.term!r} is already a synonym of {other.concept_name!r} (synonym {other.synonym_id}); "
                    "scope one of them to a category or speciality instead"
                )


def _via_synonym(synonym: SynonymMapping) -> ResolvedConcept:
    return ResolvedConcept(
        concept_level=synonym.concept_level, concept_id=synonym.concept_id, concept_name=synonym.concept_name,
        via="synonym", synonym_id=synonym.synonym_id, synonym_term=synonym.term,
        scope_level=synonym.scope_level, scope_id=synonym.scope_id,
    )
