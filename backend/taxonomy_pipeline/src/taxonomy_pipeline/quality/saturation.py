"""Taxonomy saturation: how many genuinely new concepts each incoming source adds.

Incoming sources are compared in order. Source k is checked against the
baseline plus every concept from sources 1..k-1, so a falling new-items rate
across steps (the `curve`) is evidence the taxonomy is approaching saturation.

Each unique incoming concept (named rows grouped by `match_key`) gets one
verdict. First, is there a similar concept at any level of what is known?
Evidence tiers run strongest first - exact key, close spelling confirmed by
the meaning model, meaning alone - and within a tier the same level wins.
Close spelling alone never counts: "evaluation" is not "valuation".

- Similar concept found -> check parents (within each concept's own source):
  - no incoming parent, or a tag (tags are shared keywords) -> skip
  - found at another level -> REVIEW_OTHER_LEVEL: parents are different
    kinds (e.g. a sector vs a tag's specialities) and can't be compared
  - every incoming parent similar to a parent of the known concept -> skip
  - otherwise -> REVIEW_NEW_PLACEMENT: known concept, new context
    (e.g. "Machine Learning" under Finance vs under Healthcare)
- Nothing similar -> ADD_CANDIDATE when nothing at any level is even
  moderately close, otherwise REVIEW_AMBIGUOUS.
- Optional judge (`judge` param, off by default; see `judge.py`): each
  REVIEW_AMBIGUOUS concept and its closest known concepts by meaning go to an
  LLM. A confident "same as candidate N" becomes a duplicate (`match_type:
  judged`), a confident "none" an ADD_CANDIDATE, anything else stays
  REVIEW_AMBIGUOUS. Both counts are reported (`review_ambiguous_before_judge`).

New placements and other-level matches are reported separately and do not
count toward the new-items rate. Every recommendation is a review aid, not a database
operation. Every incoming concept - duplicates included - carries its parents
within its own source, so a downstream integration team knows where each one
hangs off (e.g. which SkillsFuture job roles link to a skill we already have).
"""
from __future__ import annotations

from typing import Any

from ..matching import embeddings
from ..matching.engine import (
    ADD_CANDIDATE,
    CONCEPT_LEVELS,
    REVIEW_AMBIGUOUS,
    REVIEW_OTHER_LEVEL,
    KnownPool,
    SourceParents,
    compare_level,
    group_concepts,
    rate,
    validated_float,
)
from ..matching.judge import DecisionsJudge, JudgeError
from ..matching.semantic import SemanticMatcher
from ..models import Measure, MetricResult
from ..taxonomy.base import LoadedDataset, TaxonomyDataset
from ..taxonomy.synonym_set import SynonymMapping, SynonymSet
from .base import Metric


def _level_thresholds(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        raise ValueError("semantic_threshold_by_level must map concept levels to thresholds")
    unknown = sorted(set(value) - set(CONCEPT_LEVELS))
    if unknown:
        raise ValueError(f"semantic_threshold_by_level has unknown levels {unknown}; expected {CONCEPT_LEVELS}")
    return {
        level: validated_float(value, level, -1, 1) for level in CONCEPT_LEVELS if level in value
    }


JUDGE_DEFAULTS = {
    "model": "gpt-6-luna", "candidates": 5, "same_at_or_above": 0.8, "new_at_or_above": 0.8,
    "levels": ["tags"], "cache": None, "workers": 8,
}


def _judge_settings(value: Any) -> dict[str, Any] | None:
    """Validated `judge` params (None leaves the judge off), defaults filled in."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("judge must be a mapping of settings, or omitted to leave the judge off")
    unknown = sorted(set(value) - set(JUDGE_DEFAULTS))
    if unknown:
        raise ValueError(f"judge has unknown settings {unknown}; expected {sorted(JUDGE_DEFAULTS)}")
    cfg = {**JUDGE_DEFAULTS, **value}
    if not isinstance(cfg["model"], str) or not cfg["model"].strip():
        raise ValueError("judge.model must be a model name")
    for name, low in (("candidates", 1), ("workers", 1)):
        if isinstance(cfg[name], bool) or not isinstance(cfg[name], int) or cfg[name] < low:
            raise ValueError(f"judge.{name} must be a whole number of at least {low}")
    for name in ("same_at_or_above", "new_at_or_above"):
        try:
            cfg[name] = validated_float(cfg, name, 0, 1)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"judge.{exc}") from exc
    levels = cfg["levels"]
    if not isinstance(levels, list) or not levels or not set(levels) <= set(CONCEPT_LEVELS):
        raise ValueError(f"judge.levels must be a non-empty list drawn from {list(CONCEPT_LEVELS)}")
    if cfg["cache"] is not None and not (isinstance(cfg["cache"], str) and cfg["cache"].strip()):
        raise ValueError("judge.cache must be a file path, or omitted for no cache")
    return cfg


def _incoming_names(value: Any, baseline: Any) -> list[str]:
    names = [value] if isinstance(value, str) else value
    if not isinstance(names, list) or not names or not all(isinstance(n, str) and n for n in names):
        raise ValueError("incoming must be a dataset name or a non-empty list of dataset names")
    if len(set(names)) != len(names):
        raise ValueError(f"incoming lists a dataset more than once: {names}")
    if baseline in names:
        raise ValueError(f"incoming must not include the baseline dataset {baseline!r}")
    return names


class SaturationMetric(Metric):
    id = "saturation"
    subject_param = "baseline"
    description = (
        "New-concept rate of each incoming source, in order, against the baseline plus earlier sources, "
        "matching at every level. Known concepts under a different parent are flagged as new placements; "
        "unknown ones are add candidates or review items, with their parents in their own source."
    )
    default_params = {
        "baseline": None,
        "incoming": None,
        "threshold": 90.0,
        "semantic_model": None,
        # Hugging Face commit to load `semantic_model` at. None uses the pin in
        # embeddings.PINNED_REVISIONS for a model listed there, else the model's default branch.
        "semantic_model_revision": None,
        "semantic_threshold": 0.7,
        "add_candidate_lexical_threshold": 60.0,
        "add_candidate_semantic_threshold": 0.5,
        "cross_level": True,
        "semantic_threshold_by_level": {},
        # Off unless set; see JUDGE_DEFAULTS and matching/judge.py.
        "judge": None,
    }

    def definition(self) -> dict[str, Any]:
        # A `judge` mapping only names the settings it changes; JUDGE_DEFAULTS fill in the rest. A model left
        # unpinned in a config loads at the revision pinned in code, so the pins are part of the definition too.
        return {
            **super().definition(), "judge_defaults": dict(JUDGE_DEFAULTS),
            "pinned_model_revisions": dict(embeddings.PINNED_REVISIONS),
        }

    def measures(self, result: MetricResult) -> list[Measure]:
        """How much of the incoming sources the taxonomy still lacks, after the last source (the curve's last
        point), lower is better: fewer add candidates and a lower new-items rate mean the taxonomy already
        covers more of them. How many concepts were reviewed, placed anew or found at another level is
        context, with no better direction."""
        curve = result.details.get("curve") or [{}]
        last = curve[-1]
        return [
            Measure(key="add_candidates", label="Add candidates", value=last.get("add_candidates"), better="lower"),
            Measure(key="review_ambiguous", label="Concepts needing review", value=last.get("review_ambiguous"),
                    better="lower"),
            Measure(key="new_rate_min", label="New-items rate (min)", value=last.get("new_rate_min"), better="lower",
                    unit="rate"),
            Measure(key="new_rate_max", label="New-items rate (max)", value=last.get("new_rate_max"), better="lower",
                    unit="rate"),
            Measure(key="reviewed", label="Incoming concepts reviewed", value=last.get("reviewed")),
            Measure(key="new_placements", label="Known concepts in a new placement",
                    value=last.get("new_placements")),
            Measure(key="review_other_level", label="Known concepts at another level",
                    value=last.get("review_other_level")),
        ]

    def run(self, datasets: dict[str, LoadedDataset], params: dict[str, Any]) -> MetricResult:
        p = self.resolve_params(params)
        try:
            incoming_names = _incoming_names(p["incoming"], p["baseline"])
        except ValueError as exc:
            return MetricResult(metric_id=self.id, status="error", summary=str(exc))
        roles = {"baseline": p["baseline"], **{f"incoming[{i}]": n for i, n in enumerate(incoming_names)}}
        resolved = self.require_datasets(datasets, roles, {role: TaxonomyDataset for role in roles})
        if isinstance(resolved, MetricResult):
            return resolved

        try:
            settings = {
                "threshold": validated_float(p, "threshold", 0, 100),
                "semantic_threshold": validated_float(p, "semantic_threshold", -1, 1),
                "add_candidate_lexical_threshold": validated_float(p, "add_candidate_lexical_threshold", 0, 100),
                "add_candidate_semantic_threshold": validated_float(p, "add_candidate_semantic_threshold", -1, 1),
            }
        except (TypeError, ValueError) as exc:
            return MetricResult(metric_id=self.id, status="error", summary=str(exc))
        if not isinstance(p["cross_level"], bool):
            return MetricResult(metric_id=self.id, status="error", summary="cross_level must be true or false")
        settings["cross_level"] = p["cross_level"]
        try:
            settings["semantic_threshold_by_level"] = _level_thresholds(p["semantic_threshold_by_level"])
            judge_cfg = _judge_settings(p["judge"])
        except (TypeError, ValueError) as exc:
            return MetricResult(metric_id=self.id, status="error", summary=str(exc))
        if judge_cfg and not p["semantic_model"]:
            return MetricResult(metric_id=self.id, status="error", summary=(
                "judge needs semantic_model: the meaning model picks the candidates the judge chooses from"
            ))
        revision = p["semantic_model_revision"]
        if revision is not None and not (isinstance(revision, str) and revision.strip()):
            return MetricResult(metric_id=self.id, status="error", summary=(
                "semantic_model_revision must be a model commit, branch or tag, or omitted"
            ))
        if revision is not None and not p["semantic_model"]:
            return MetricResult(metric_id=self.id, status="error", summary=(
                "semantic_model_revision needs semantic_model: it pins that model's version"
            ))

        synonyms: list[SynonymMapping] = []
        if p.get("synonyms") is not None:
            # Opt-in and read from a frozen `synonym_set` dataset, so the same config always scores the same (#60).
            found = self.require_datasets(datasets, {"synonyms": p["synonyms"]}, {"synonyms": SynonymSet})
            if isinstance(found, MetricResult):
                return found
            synonyms = found["synonyms"].synonyms

        baseline = resolved["baseline"]
        incoming = [resolved[f"incoming[{i}]"] for i in range(len(incoming_names))]
        for source in incoming:
            if not any(group_concepts(getattr(source, level))[0] for level in CONCEPT_LEVELS):
                return MetricResult(metric_id=self.id, status="error", summary=(
                    f"{source.name} has no named categories, specialities, or tags to compare; "
                    "check the dataset source or table_map."
                ))
        empty_baseline_levels = [
            level for level in CONCEPT_LEVELS
            if not group_concepts(getattr(baseline, level))[0]
            and any(group_concepts(getattr(source, level))[0] for source in incoming)
        ]
        if empty_baseline_levels:
            return MetricResult(metric_id=self.id, status="error", summary=(
                f"{baseline.name} has no named records for {', '.join(empty_baseline_levels)} while "
                "an incoming source does; check the dataset source or table_map."
            ))
        if p["semantic_model"] and revision is None:
            # Recorded as loaded: an unpinned config still scores with the model version pinned in code.
            revision = embeddings.pinned_revision(str(p["semantic_model"]))
        try:
            semantic_matcher = (
                SemanticMatcher(str(p["semantic_model"]), revision=revision) if p["semantic_model"] else None
            )
        except ValueError as exc:
            return MetricResult(metric_id=self.id, status="error", summary=str(exc))
        if judge_cfg:
            try:
                judge = DecisionsJudge(judge_cfg["model"], workers=judge_cfg["workers"], cache_path=judge_cfg["cache"])
            except JudgeError as exc:
                return MetricResult(metric_id=self.id, status="error", summary=f"judge: {exc}")
            settings["judge"] = (judge, judge_cfg)

        try:
            steps, curve, pool, aliases = self._compare_sources(
                baseline, incoming_names, incoming, settings, semantic_matcher, synonyms,
            )
        except JudgeError as exc:
            return MetricResult(metric_id=self.id, status="error", summary=f"judge: {exc}")
        last = curve[-1]
        summary = (
            f"{last['source']} against {baseline.name}"
            f"{' + earlier sources' if len(curve) > 1 else ''}: new-items rate "
            f"{last['new_rate_min']:.1%}-{last['new_rate_max']:.1%} of {last['reviewed']} concepts "
            f"({last['add_candidates']} add candidates, {last['review_ambiguous']} need review); "
            f"{last['new_placements']} known concepts in a new placement, "
            f"{last['review_other_level']} known at another level."
        )
        if judge_cfg:
            counts = last["judge_counts"]
            summary += (
                f" Judge ({judge_cfg['model']}) settled {counts['judged_duplicate'] + counts['judged_new']} of "
                f"{counts['asked']} ambiguous concepts it was asked about ({', '.join(judge_cfg['levels'])}): "
                f"{counts['judged_duplicate']} duplicates, "
                f"{counts['judged_new']} new, {counts['unsure']} unsure, {counts['failed']} failed."
            )
        if len(curve) > 1:
            summary += " Curve: " + ", ".join(
                f"{point['source']} {point['new_rate_min']:.1%}-{point['new_rate_max']:.1%}" for point in curve
            ) + "."
        return MetricResult(
            metric_id=self.id,
            summary=summary,
            details={
                "baseline": baseline.name,
                "incoming": incoming_names,
                "threshold": settings["threshold"],
                "semantic_model": p["semantic_model"],
                "semantic_model_revision": revision,
                "semantic_threshold": settings["semantic_threshold"] if semantic_matcher else None,
                "add_candidate_lexical_threshold": settings["add_candidate_lexical_threshold"],
                "add_candidate_semantic_threshold": (
                    settings["add_candidate_semantic_threshold"] if semantic_matcher else None
                ),
                "overall": steps[-1]["overall"],
                "by_concept_level": steps[-1]["by_concept_level"],
                "cross_level": p["cross_level"],
                "semantic_threshold_by_level": (
                    settings["semantic_threshold_by_level"] if semantic_matcher else None
                ),
                **({"judge": judge_cfg} if judge_cfg else {}),
                **({"synonyms": {"dataset": p["synonyms"], "global_synonyms_applied": aliases}}
                   if p.get("synonyms") is not None else {}),
                "curve": curve,
                "steps": steps,
            },
        )

    @staticmethod
    def _compare_sources(
        baseline: TaxonomyDataset, incoming_names: list[str], incoming: list[TaxonomyDataset],
        settings: dict[str, Any], semantic_matcher: SemanticMatcher | None,
        synonyms: list[SynonymMapping] | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], KnownPool, int]:
        """Compare each incoming source in order against everything known before it; the baseline's
        global synonyms (if any) count as names of its concepts. Also returns how many were applied."""
        judged = bool(settings.get("judge"))
        pool = KnownPool()
        pool.add(baseline.name, baseline)
        aliases = pool.add_aliases(synonyms or [])
        steps, curve = [], []
        for number, (name, source) in enumerate(zip(incoming_names, incoming, strict=True), start=1):
            parents = SourceParents(source)
            seen_in_source: dict[str, str] = {}
            by_level = {
                level: compare_level(
                    level, getattr(source, level), pool, parents, settings, semantic_matcher, seen_in_source
                )
                for level in CONCEPT_LEVELS
            }
            pool.add(name, source)
            reviewed = sum(result["relevant_concepts_reviewed"] for result in by_level.values())
            ambiguous = sum(result["ambiguous"] for result in by_level.values())
            adds = sum(result["recommended_action_counts"][ADD_CANDIDATE] for result in by_level.values())
            placements = sum(result["new_placements"] for result in by_level.values())
            other_level = sum(result["recommended_action_counts"][REVIEW_OTHER_LEVEL] for result in by_level.values())
            checked = sum(result["placements_checked"] for result in by_level.values())
            steps.append({
                "source": name,
                "overall": {
                    "relevant_concepts_reviewed": reviewed,
                    "ambiguous": ambiguous,
                    "ambiguous_rate": rate(ambiguous, reviewed),
                },
                "by_concept_level": by_level,
            })
            curve.append({
                "step": number,
                "source": name,
                "reviewed": reviewed,
                "add_candidates": adds,
                "review_ambiguous": ambiguous - adds,
                "new_rate_min": rate(adds, reviewed),
                "new_rate_max": rate(ambiguous, reviewed),
                "new_placements": placements,
                "new_placement_rate": rate(placements, checked),
                "review_other_level": other_level,
                "cumulative_known_concepts": pool.size(),
            })
            if judged:
                curve[-1]["review_ambiguous_before_judge"] = sum(
                    result.get("review_ambiguous_before_judge", result["recommended_action_counts"][REVIEW_AMBIGUOUS])
                    for result in by_level.values()
                )
                curve[-1]["judge_counts"] = {
                    key: sum(result.get("judge_counts", {}).get(key, 0) for result in by_level.values())
                    for key in ("asked", "judged_duplicate", "judged_new", "unsure", "failed")
                }

        return steps, curve, pool, aliases
