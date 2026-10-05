"""Run Case A (category adaptation) end to end against real data and write a
JSON output file validated against CLASSIFICATION_OUTPUT_CONTRACT.md.

Bryan's "Route outcome" / "Has source categories?" routing doesn't exist as
pipeline code yet, so this script stands in for it: run saturation to find
which incoming tags are new, feed those into category_adaptation, then
serialize the result into the shared classification output contract.

Usage (from backend/taxonomy_pipeline/):
    .venv/bin/python scripts/run_case_a.py
    .venv/bin/python scripts/run_case_a.py --config configs/original_vs_skillsfuture.yaml \
        --baseline original --incoming skillsfuture --output case_a_output.json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from taxonomy_pipeline.classification.from_category_adaptation import build_classification_outputs
from taxonomy_pipeline.classification.models import CLASSIFICATION_OUTPUTS
from taxonomy_pipeline.config import load_config
from taxonomy_pipeline.metrics.category_adaptation import CategoryAdaptationMetric
from taxonomy_pipeline.metrics.saturation import SaturationMetric
from taxonomy_pipeline.pipeline import load_datasets


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/original_vs_skillsfuture.yaml")
    parser.add_argument("--baseline", default="original")
    parser.add_argument("--incoming", default="skillsfuture")
    parser.add_argument("--semantic-model", default="all-MiniLM-L6-v2")
    parser.add_argument("--output", default="case_a_output.json")
    args = parser.parse_args()

    config = load_config(args.config)
    datasets = load_datasets(config)

    # Step 1 (Lionel's saturation): which incoming tags have no confident
    # match in the baseline at all. Of those, only the ones Lionel's own
    # parent check already marked HAS_PARENT actually have a source category
    # to place - NO_PARENT tags are exactly Case B's job (see parent_status
    # on each ambiguous entry, computed the same way for every concept
    # level). Routing on his field directly, instead of re-deriving "does
    # this tag have a category" ourselves, keeps one source of truth instead
    # of two copies of the same check that could quietly drift apart.
    saturation = SaturationMetric().run(
        datasets, {"baseline": args.baseline, "incoming": [args.incoming], "semantic_model": args.semantic_model}
    )
    if saturation.status != "ok":
        print(f"saturation failed: {saturation.summary}", file=sys.stderr)
        return 1
    add_candidates = [
        c
        for c in saturation.details["by_concept_level"]["tags"]["ambiguous_concepts"]
        if c["recommended_action"] == "ADD_CANDIDATE"
    ]
    # incoming_ids (plural), not incoming_id: a saturation entry groups every
    # tag sharing a normalized name under one record, so using the singular
    # field would silently drop every id but the first in such a group.
    new_tag_ids = [tag_id for c in add_candidates if c["parent_status"] == "HAS_PARENT" for tag_id in c["incoming_ids"]]
    route_to_case_b_tag_ids = [
        tag_id for c in add_candidates if c["parent_status"] == "NO_PARENT" for tag_id in c["incoming_ids"]
    ]
    print(
        f"saturation: {len(add_candidates)} new concepts "
        f"({len(new_tag_ids)} tag ids with a parent -> Case A, "
        f"{len(route_to_case_b_tag_ids)} tag ids with no parent -> Case B)",
        file=sys.stderr,
    )

    # Step 2 (Case A): place each new tag's source category and speciality
    # onto the baseline.
    result = CategoryAdaptationMetric().run(
        datasets,
        {"baseline": args.baseline, "incoming": args.incoming, "semantic_model": args.semantic_model,
         "new_tag_ids": new_tag_ids},
    )
    if result.status != "ok":
        print(f"category_adaptation failed: {result.summary}", file=sys.stderr)
        return 1
    print(f"category_adaptation: {result.summary}", file=sys.stderr)

    # Step 3: serialize into the shared contract and validate it the same
    # way Bryan's side will - if this fails, the output is wrong, not just
    # the file write.
    records = build_classification_outputs(result.details["placements"], source_dataset=args.incoming)
    payload = CLASSIFICATION_OUTPUTS.dump_json(records, indent=2)
    CLASSIFICATION_OUTPUTS.validate_json(payload)  # round-trip check before writing

    output_path = Path(args.output)
    output_path.write_bytes(payload)
    resolved = sum(1 for r in records if r.category is not None)
    print(f"wrote {output_path}: {len(records)} records ({resolved} resolved, {len(records) - resolved} unresolved)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
