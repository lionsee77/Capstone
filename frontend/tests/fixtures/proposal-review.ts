/** Saved proposal evidence and a separate reviewer choice; no approval UI is implied. */
export const proposalReview = {
  baseline_snapshot_id: "snapshot",
  settings: { lexical_threshold: 0.9, semantic_threshold: 0.7 },
  semantic_status: "complete",
  skipped_checks: {},
  total: 1,
  offset: 0,
  limit: 50,
  proposals: [{
    proposed_path: ["Subject", "Computing", "Data visualizatio"],
    proposed_parent: ["Subject", "Computing"],
    proposal: {
      proposal_id: "proposal-1", level: "speciality", name: "Data visualizatio",
      path: ["Subject", "computing", "data visualizatio"],
      variants: [["Subject", "Computing", "Data visualizatio"]],
      source_tags: [["feed", "1"]], origins: [["case.json", 0]],
      status: "review", suggested_action: "defer", existing_id: null, blocks_creation: true,
      reason: "Near match requires review",
      matches: [{
        concept: {
          reference: "existing-20", level: "speciality", id: 20, name: "Data visualization",
          path: ["Subject", "Computing", "Data visualization"], path_ids: [1, 10, 20], reusable: true,
        },
        match_types: ["lexical", "semantic"], lexical_score: 0.97, semantic_score: 0.8, same_parent: true,
      }],
    },
    decision: { proposal_id: "proposal-1", action: "reuse", reason: "Reviewed", existing_concept_id: 20 },
    resolved_node: {
      level: "speciality", id: 20, parent_id: 10, name: "Data visualization",
      path: ["Subject", "computing", "data visualizatio"], existing: true,
    },
  }],
};
