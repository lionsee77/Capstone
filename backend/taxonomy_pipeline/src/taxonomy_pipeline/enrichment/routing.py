"""Split one saved saturation report between Case A and Case B.

Saturation runs once per classification round (`taxonomy-pipeline run
--output report.json`) and both producers read that same file, so they always
divide the same set of tags: tags with a parent in their own source go to Case A,
tags without one go to Case B. Keeping the rule here, in one place, means the two
halves cannot overlap or leave a gap.

Both `ADD_CANDIDATE` tags (nothing in the baseline is close) and `REVIEW_AMBIGUOUS`
tags (something is moderately close, so saturation could not confirm the tag is
new) are routed. The producers place ambiguous tags like any other, but flag every
placement `needs_review` and name the close baseline concept in its `reason`
(`mark_ambiguous`), so merge-preview holds them until a reviewer decides whether
each one is new (`create`), an existing tag (`reuse`), or neither (`exclude`/`defer`).
Duplicates (`SKIP_*`) and other review outcomes are not routed.

Each producer stamps the report's `run_id` into its records as
`saturation_run_id`; the merge-preview runner refuses to combine outputs from
different runs.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ADD_CANDIDATE = "ADD_CANDIDATE"
REVIEW_AMBIGUOUS = "REVIEW_AMBIGUOUS"
ROUTED_ACTIONS = {ADD_CANDIDATE, REVIEW_AMBIGUOUS}
RUN_ID_FIELD = "saturation_run_id"
_LEVEL_NOUNS = {"services": "service", "categories": "category", "specialities": "speciality", "tags": "tag"}


class SaturationReport:
    """The saturation step for one incoming source, read from a saved run report."""

    def __init__(self, run_id: str | None, incoming: str, step: dict[str, Any]):
        self.run_id = run_id
        self.incoming = incoming
        self.step = step

    def tag_ids(self, actions: set[str], parent_status: str | None = None) -> list[Any]:
        """Incoming tag IDs with a matching verdict, in report order, without repeats.

        `incoming_ids` (plural) is used because one saturation entry groups every
        source tag sharing a normalized name.
        """
        concepts = self.step["by_concept_level"]["tags"]["ambiguous_concepts"]
        tag_ids = [
            tag_id
            for concept in concepts
            if concept.get("recommended_action") in actions
            and (parent_status is None or concept.get("parent_status") == parent_status)
            for tag_id in concept.get("incoming_ids", [])
        ]
        return list(dict.fromkeys(tag_ids))

    def ambiguous_matches(self) -> dict[str, dict[str, Any]]:
        """Each `REVIEW_AMBIGUOUS` tag's saturation entry, keyed by tag ID as a string."""
        return {
            str(tag_id): concept
            for concept in self.step["by_concept_level"]["tags"]["ambiguous_concepts"]
            if concept.get("recommended_action") == REVIEW_AMBIGUOUS
            for tag_id in concept.get("incoming_ids", [])
        }


def load_saturation(path: str | Path, incoming: str) -> SaturationReport:
    """Read the saturation step for `incoming` from a `taxonomy-pipeline run` report.

    Fails loudly rather than guessing: a missing or malformed report, or one
    that never compared `incoming`, would silently route the wrong tags.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Saturation report not found: {path}. Generate it first with "
            "`taxonomy-pipeline run --config <config> --output report.json`."
        )
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        saturation = next(result for result in report["results"] if result.get("metric_id") == "saturation")
        if saturation.get("status", "ok") != "ok":
            raise ValueError(f"saturation did not run successfully: {saturation.get('summary')}")
        details = saturation["details"]
        # Older reports have no per-source `steps`; their top level describes the single incoming
        # source, and `incoming` was a plain name rather than a list.
        sources = details.get("incoming") or [None]
        sources = [sources] if isinstance(sources, str) else sources
        steps = details.get("steps") or [{**details, "source": sources[-1]}]
        step = next((step for step in steps if step.get("source") == incoming), None)
    except (json.JSONDecodeError, KeyError, StopIteration, TypeError, AttributeError) as exc:
        raise ValueError(f"Invalid saturation report: {path} ({type(exc).__name__}: {exc})") from exc
    if step is None:
        sources = [step.get("source") for step in steps]
        raise ValueError(f"Saturation report {path} has no results for incoming source {incoming!r} (has {sources})")
    return SaturationReport(run_id=report.get("run_id"), incoming=incoming, step=step)


def _require(tag_ids: list[Any], report: SaturationReport, parent_status: str, case: str) -> list[Any]:
    if not tag_ids:
        raise ValueError(
            f"No {case} tags in the saturation report for {report.incoming!r}; expected "
            f"recommended_action in {sorted(ROUTED_ACTIONS)} and parent_status={parent_status}."
        )
    return tag_ids


def case_a_tag_ids(report: SaturationReport) -> list[Any]:
    """New or possibly new tags that have a category of their own in the incoming source."""
    return _require(report.tag_ids(ROUTED_ACTIONS, "HAS_PARENT"), report, "HAS_PARENT", "Case A")


def case_b_tag_ids(report: SaturationReport) -> list[Any]:
    """New or possibly new tags with nothing of their own to place against; Case B classifies them with an LLM."""
    return _require(report.tag_ids(ROUTED_ACTIONS, "NO_PARENT"), report, "NO_PARENT", "Case B")


def _similarity_note(concept: dict[str, Any]) -> str:
    """What saturation found close to an ambiguous tag, for the reviewer."""
    hints = []
    semantic, lexical = concept.get("closest_semantic_baseline_name"), concept.get("closest_baseline_name")
    if semantic:
        level = _LEVEL_NOUNS.get(concept.get("closest_semantic_baseline_level"), "concept")
        hints.append(f"{level} '{semantic}' in meaning ({concept.get('closest_semantic_score')})")
    if lexical and lexical != semantic:
        level = _LEVEL_NOUNS.get(concept.get("closest_baseline_level"), "concept")
        hints.append(f"{level} '{lexical}' in spelling ({concept.get('closest_baseline_score')})")
    similar = f" It is close to existing {' and '.join(hints)}." if hints else ""
    return (
        f" Needs review: saturation could not confirm this tag is new ({concept.get('reason')}).{similar} "
        "Decide whether it is new (create, approving the right path), an existing tag (reuse), or neither "
        "(exclude/defer)."
    )


def mark_ambiguous(records: list[dict[str, Any]], report: SaturationReport) -> list[dict[str, Any]]:
    """Flag every placed record of a `REVIEW_AMBIGUOUS` tag `needs_review`, with saturation's evidence.

    Both producers call this on their contract records, so an ambiguous tag is held
    for review whichever case placed it. Unresolved records (no placement) are left
    as they are: the contract only allows the flag on a placement a reviewer can approve.
    """
    ambiguous = report.ambiguous_matches()
    marked = []
    for record in records:
        concept = ambiguous.get(str(record["tag_id"]))
        if concept is not None and record.get("category") is not None:
            record = {**record, "needs_review": True, "reason": record["reason"] + _similarity_note(concept)}
        marked.append(record)
    return marked
