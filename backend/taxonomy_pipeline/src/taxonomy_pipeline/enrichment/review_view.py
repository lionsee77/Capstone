"""Read saved proposal evidence without matching, applying decisions or writing audit events.

File-backed merge runs are the current source. A future proposal store can call
build_review_view with the same report; CLI and API do not implement separate views.
"""
from __future__ import annotations

from pathlib import Path

from .models import ProposalReviewItem, ProposalReviewReport, ResolutionReport
from .proposals.models import Level
from .review import prepare_hierarchy_decisions


class ProposalNotFound(ValueError):
    """Distinguish an absent proposal from an unusable saved report at the HTTP boundary."""


# Select saved proposals with all matching branches and the existing unified review decisions.
def build_review_view(
    resolution: ResolutionReport, *, proposal_id: str | None = None,
    level: Level | None = None, offset: int = 0, limit: int = 50,
) -> ProposalReviewReport:
    if offset < 0 or not 1 <= limit <= 200:
        raise ValueError("offset must be nonnegative and limit must be between 1 and 200")
    if level not in {None, "category", "speciality"}:
        raise ValueError("level must be category or speciality")
    if proposal_id is not None and (offset or level is not None):
        # Filtering or paging one proposal could only hide it; ask for one or the other.
        raise ValueError("a proposal ID selects one proposal; don't combine it with a level or an offset")
    screening = resolution.screening
    if screening is None:
        raise ValueError("Saved run has no hierarchy screening; rerun merge-preview before review")
    if screening.baseline_snapshot_id != resolution.baseline_snapshot_id:
        raise ValueError("Saved screening and resolution reference different baselines; rerun merge-preview")
    decisions = {choice.proposal_id: choice
                 for choice in prepare_hierarchy_decisions(resolution.decisions, screening)}
    nodes = {(node.level, tuple(node.path)): node for node in resolution.hierarchy}
    proposals = sorted(screening.proposals, key=lambda item: (item.level, item.path, item.proposal_id))
    if proposal_id is not None:
        proposals = [item for item in proposals if item.proposal_id == proposal_id]
        if not proposals:
            raise ProposalNotFound("Proposal not found in this merge run")
    if level is not None:
        proposals = [item for item in proposals if item.level == level]
    return ProposalReviewReport(
        baseline_snapshot_id=resolution.baseline_snapshot_id, settings=screening.settings,
        semantic_status=screening.semantic_status, skipped_checks=screening.skipped_checks,
        total=len(proposals), offset=offset, limit=limit,
        proposals=[ProposalReviewItem(
            proposal=item, proposed_path=min(item.variants) if item.variants else item.path,
            proposed_parent=(min(item.variants) if item.variants else item.path)[:-1],
            decision=decisions.get(item.proposal_id),
            resolved_node=nodes.get((item.level, tuple(item.path))),
        ) for item in proposals[offset:offset + limit]],
    )


# Load only the saved resolution report so review never depends on mutable source datasets or model availability.
def load_review_view(
    merge_run: str | Path, *, proposal_id: str | None = None,
    level: Level | None = None, offset: int = 0, limit: int = 50,
) -> ProposalReviewReport:
    resolution = ResolutionReport.model_validate_json(
        (Path(merge_run) / "resolution.json").read_text(encoding="utf-8"),
    )
    return build_review_view(resolution, proposal_id=proposal_id, level=level, offset=offset, limit=limit)
