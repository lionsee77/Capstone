"""What a term means in one taxonomy: its own concept names plus approved synonyms (#61, used by #60).

A term is compared by `match_key`, like stored synonyms (`term_key`). Candidates are
taken in tiers, most specific first, and the first tier with any candidate decides:

1. synonyms scoped to the branch the lookup is made in (a speciality's scope before
   its category's), so a scoped synonym can give a word a local meaning;
2. concept names: a term that *is* a concept's name means that concept;
3. global synonyms.

If the deciding tier names more than one concept, the term is `ambiguous` and every
candidate is returned, one match per concept: the resolver never picks one. Two
concepts sharing a name (the baseline has such duplicates) are ambiguous in the same way.

A stored synonym only counts where it still fits this taxonomy: its concept exists with
the name the synonym was recorded against, and a scoped synonym's concept still sits in
its scope. Synonyms that don't fit (the concept was renamed or removed, or the dataset
name now points at another taxonomy) are set aside in `stale` instead of resolving to
the wrong concept; `problems()` lists them with any ambiguous global synonyms.

`check_new` enforces the rules that keep global synonyms unambiguous: a global
synonym may not be another concept's name or another concept's global synonym, so a
word with several meanings has to be scoped. The index is pure data, built from a
taxonomy and the synonyms the caller loaded (stored `Synonym` rows, of which only
approved ones count, or `SynonymMapping`s from screening settings or an exported synonym
set); it never reads storage itself.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel

from ..matching.names import match_key
from ..taxonomy.base import TaxonomyDataset
from ..taxonomy.synonym_set import SynonymMapping
from .models import AUDIT_LEVELS, Resolution, ResolvedConcept, Synonym, SynonymCreate
from .store import SynonymError

LEVELS = tuple(AUDIT_LEVELS)
# Deeper scopes are more specific, so they're consulted first.
SCOPE_DEPTH = {"specialities": 0, "categories": 1}


class ConflictingSynonym(SynonymError):
    """The term already means another concept where the new synonym would apply."""


class InvalidScope(SynonymError):
    """The scope or lookup branch doesn't exist, or the concept doesn't sit inside the scope."""


class SynonymProblem(BaseModel):
    """A stored synonym that won't resolve as recorded, or makes a term ambiguous; `problem` says which."""

    synonym_id: str
    term: str
    concept_level: str
    concept_id: str
    concept_name: str
    problem: str


class SynonymIndex:
    """Concept names and fitting approved synonyms of one taxonomy, folded once for lookups."""

    def __init__(self, taxonomy: TaxonomyDataset, synonyms: Iterable[Synonym | SynonymMapping] = ()) -> None:
        self.taxonomy = taxonomy
        self.hierarchy = taxonomy.hierarchy()
        # Every row is a concept, named or not; a repeated ID is its last row, as in Hierarchy.
        self.concepts: dict[tuple[str, str], dict[str, Any]] = {
            (level, str(row.get("id"))): row for level in LEVELS for row in getattr(taxonomy, level)
        }
        self.names: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
        for (level, concept_id), row in self.concepts.items():
            name = row.get("name")
            if isinstance(name, str) and match_key(name):
                self.names[match_key(name)].append((level, concept_id, name))
        self.global_synonyms: dict[str, list[SynonymMapping]] = defaultdict(list)
        self.scoped_synonyms: dict[str, list[SynonymMapping]] = defaultdict(list)
        self.stale: list[SynonymProblem] = []
        for synonym in synonyms:
            if isinstance(synonym, Synonym):
                if synonym.status != "approved":
                    continue
                synonym = synonym.mapping()
            problem = self._misfit(synonym)
            if problem:
                self.stale.append(_problem(synonym, problem))
                continue
            target = self.scoped_synonyms if synonym.scope else self.global_synonyms
            target[synonym.term_key].append(synonym)

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

    def resolve(self, term: str, *, level: str | None = None, within: tuple[str, Any] | None = None) -> Resolution:
        """What `term` means, optionally only at `level`, for a lookup made inside branch `within`
        (a category, speciality or tag). An unknown branch is InvalidScope, never a silent global lookup."""
        key = match_key(term)
        wanted = (lambda candidate: candidate == level) if level else (lambda _: True)
        tiers: list[list[ResolvedConcept]] = []
        if within is not None:
            within = (within[0], str(within[1]))
            if within not in self.concepts:
                raise InvalidScope(f"{self.taxonomy.name} has no {AUDIT_LEVELS.get(within[0], within[0])} "
                                   f"with ID {within[1]} to look inside")
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
            unique = list({(m.concept_level, m.concept_id): m for m in reversed(matches)}.values())[::-1]
            if unique:
                return Resolution(term=term, status="resolved" if len(unique) == 1 else "ambiguous", matches=unique)
        return Resolution(term=term, status="unknown")

    def check_new(self, request: SynonymCreate, existing: Iterable[Synonym | SynonymMapping] | None = None) -> None:
        """Refuse a synonym that would make a term ambiguous, or whose scope doesn't contain its concept.

        Global: the term may not already be another concept's name or global synonym.
        Scoped: the scope must be a branch the concept sits in (not the concept itself),
        and no other concept may have the same synonym in the same scope. `existing` are
        the approved synonyms to check against (by default, the ones this index was built
        with); only those that still fit this taxonomy count.
        """
        key, target = match_key(request.term), (request.concept_level, request.concept_id)
        if existing is None:
            others = [s for group in (self.global_synonyms, self.scoped_synonyms) for s in group.get(key, [])]
        else:
            mappings = [s.mapping() if isinstance(s, Synonym) else s for s in existing
                        if not isinstance(s, Synonym) or s.status == "approved"]
            others = [s for s in mappings if s.term_key == key and not self._misfit(s)]
        if request.scope is not None:
            if request.scope not in self.concepts:
                raise InvalidScope(f"{self.taxonomy.name} has no {AUDIT_LEVELS[request.scope[0]]} "
                                   f"with ID {request.scope[1]} to scope to")
            if request.scope == target or request.scope not in self.branches(*target):
                raise InvalidScope(f"{AUDIT_LEVELS[target[0]]} {target[1]} doesn't sit inside "
                                   f"{AUDIT_LEVELS[request.scope[0]]} {request.scope[1]}")
            for other in others:
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
        for other in others:
            if other.scope is None and (other.concept_level, other.concept_id) != target:
                raise ConflictingSynonym(
                    f"{request.term!r} is already a synonym of {other.concept_name!r} (synonym {other.synonym_id}); "
                    "scope one of them to a category or speciality instead"
                )

    def problems(self) -> list[SynonymProblem]:
        """Stored synonyms that don't resolve as recorded: stale ones, and global synonyms that are another
        concept's name (the name wins) or that two concepts share (the term is ambiguous). Synonyms added
        before these rules existed can be in that state; nothing else changes them."""
        found = list(self.stale)
        for key, synonyms in self.global_synonyms.items():
            concepts = {(s.concept_level, s.concept_id) for s in synonyms}
            for synonym in synonyms:
                target = (synonym.concept_level, synonym.concept_id)
                named = [name for name in self.names.get(key, []) if (name[0], name[1]) != target]
                if named:
                    level, concept_id, name = named[0]
                    found.append(_problem(synonym, f"shadowed: {name!r} ({AUDIT_LEVELS[level]} {concept_id}) "
                                                   "has this name"))
                elif len(concepts) > 1:
                    found.append(_problem(synonym, f"ambiguous: a global synonym of {len(concepts)} concepts"))
        return found

    def _misfit(self, synonym: SynonymMapping) -> str | None:
        """Why a stored synonym doesn't apply to this taxonomy, or None when it does."""
        row = self.concepts.get((synonym.concept_level, synonym.concept_id))
        if row is None:
            return "stale: its concept is no longer in this taxonomy"
        if match_key(str(row.get("name") or "")) != match_key(synonym.concept_name):
            return f"stale: its concept is now named {row.get('name')!r}"
        if synonym.scope and synonym.scope not in self.branches(synonym.concept_level, synonym.concept_id):
            return "stale: its concept no longer sits in its scope"
        return None


def _problem(synonym: SynonymMapping, problem: str) -> SynonymProblem:
    return SynonymProblem(synonym_id=synonym.synonym_id, term=synonym.term, concept_level=synonym.concept_level,
                          concept_id=synonym.concept_id, concept_name=synonym.concept_name, problem=problem)


def _via_synonym(synonym: SynonymMapping) -> ResolvedConcept:
    return ResolvedConcept(
        concept_level=synonym.concept_level, concept_id=synonym.concept_id, concept_name=synonym.concept_name,
        via="synonym", synonym_id=synonym.synonym_id, synonym_term=synonym.term,
        scope_level=synonym.scope_level, scope_id=synonym.scope_id,
    )
