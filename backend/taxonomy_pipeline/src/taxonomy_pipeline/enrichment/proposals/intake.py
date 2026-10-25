"""Adapt the existing hierarchy inventory without changing Case A/B producer JSON."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from ...models import HierarchyGroup
from .models import ScreenedProposal


# Give each unique proposed path a stable identity while retaining every source and label variant.
def collect_proposals(groups: Sequence[HierarchyGroup]) -> list[ScreenedProposal]:
    proposals = []
    for group in sorted(groups, key=lambda group: (len(group.path), group.path)):
        if not group.variants:
            raise ValueError("Hierarchy proposals require original label variants")
        identity = json.dumps([group.level, group.path], ensure_ascii=False, separators=(",", ":"))
        proposals.append(ScreenedProposal(
            proposal_id=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
            level=group.level, name=min(group.variants)[-1], path=list(group.path),
            variants=sorted(group.variants),
            source_tags=sorted({(source.source_dataset, source.tag_id) for source in group.members}),
            origins=sorted({(origin.input_name, origin.row_index) for origin in group.origins}),
            status="new", reason="Not yet screened",
        ))
    return proposals
