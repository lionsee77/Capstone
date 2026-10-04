# Saturation: calibrating the matching thresholds

Reference notes for future work, not a description of current behaviour. The
`saturation` metric's thresholds (`src/taxonomy_pipeline/metrics/saturation.py`,
`configs/original_vs_skillsfuture.yaml`) were set by judgement. These notes
describe how to choose them from evidence instead: hand-label a sample of
concept pairs, then pick each threshold for its measured accuracy.

## Why it matters

Every saturation verdict comes from these thresholds. Changing them shifts
`new_rate_min` / `new_rate_max`, the whole curve, and any coverage estimate
built on it (see [`saturation-coverage-options.md`](saturation-coverage-options.md)).
Until they are calibrated, every saturation number is provisional.

## What each threshold decides

| Param | Default | Config | Decides | Too low → | Too high → |
|---|---|---|---|---|---|
| `threshold` | 90 | 90 | Is the spelling close enough to be a candidate? (RapidFuzz, 0–100) | "valuation" accepted for "evaluation" | Real typos or variants missed |
| `semantic_threshold` | 0.70 | 0.70 | Do the meanings agree? Also the **parent check** for new placements. | **New concepts silently skipped** | Real synonyms sent to review |
| `semantic_threshold_by_level` | `{}` | `{specialities: 0.80}` | The same question, for one incoming level (job titles) | "Head of Sales" counted as the skill "sales" | Real role matches missed |
| `add_candidate_lexical_threshold` | 60 | 60 | Is it confidently new, or does someone need to look? (spelling) | Too few add candidates; review queue grows | Near-duplicates marked as add |
| `add_candidate_semantic_threshold` | 0.50 | 0.50 | The same, by meaning | Same as above | Same as above |

**The two kinds of mistake don't cost the same.**

- A **wrong skip** (`SKIP_*` for a concept that's actually new) is the worst.
  The concept disappears and nobody ever reviews it.
- A **wrong review** (`REVIEW_AMBIGUOUS` for a duplicate) only costs someone's
  time.
- A **wrong add** (`ADD_CANDIDATE` for a duplicate) is caught when someone
  integrates it, but wastes effort and can let a duplicate in.

So the duplicate decisions (`threshold`, `semantic_threshold`, and the
per-level override) need **high precision on skips**. The add-candidate
cut-offs need **high precision on adds**. Anything uncertain falls into
review, which is the safety net.

## Where the scores already are

No new code is needed to start. A `report.json` from
`taxonomy-pipeline run --config ... --output report.json` already records the
scores on every entry, under
`results[metric_id == "saturation"].details.by_concept_level.<level>`:

| List | Pair to label | Scores recorded |
|---|---|---|
| `exact_match_details` | `incoming_name` ↔ `baseline_name` | `score` (100), `semantic_score` |
| `fuzzy_match_details` | `incoming_name` ↔ `baseline_name` | `score` (spelling), `semantic_score` |
| `semantic_match_details` | `incoming_name` ↔ `baseline_name` | `semantic_score`, `closest_lexical_score` / `closest_lexical_baseline_name` |
| `new_placement_details`, `other_level_match_details` | `incoming_name` ↔ `baseline_name` | as for the `match_type` they carry |
| `ambiguous_concepts` | `incoming_name` ↔ `closest_baseline_name`, and ↔ `closest_semantic_baseline_name` | `closest_baseline_score`, `closest_semantic_score` |

Every entry also has `baseline_level` / `closest_*_level`, `incoming_ids`
and `recommended_action`.

Caveats:

- The report keeps only the **best** match per concept, so you can only label
  the top candidate. That's fine, because the verdicts are made on the top
  candidate too.
- In an entry, the best spelling match and the best meaning match can be
  **different known concepts** (e.g. `closest_baseline_name` ≠
  `closest_semantic_baseline_name`). Label each one as its own pair.
- Exact matches all score 100, so they say nothing about the spelling
  threshold. Spot-check a few to confirm the match-key normalisation is
  sound, but spend most labelling effort elsewhere.

## Method

### 1. Sample pairs

- **Split by level** (categories / specialities / tags), because job titles
  and skills behave differently. That's the reason the 0.80 override exists.
- **Split by score**, with extra samples near the current thresholds. That's
  where the decisions change. For example, for meaning scores:
  - 0.50–0.60: 15%
  - 0.60–0.70: 25%
  - 0.70–0.80: 25%
  - 0.80–0.90: 20%
  - ≥ 0.90: 15%

  Do the same around 60 and 90 for spelling scores.
- **Size:** about **200–300 pairs per level**. Fewer than ~50 pairs near a
  threshold gives a precision estimate too noisy to act on.
- Fix the random seed and save the sample, so it can be reproduced.

### 2. Label

Suggested CSV layout:

```
pair_id, level, incoming_name, candidate_name, candidate_level,
lexical_score, semantic_score, label_a, label_b, label_final, note
```

Labels:

- `same`: the same concept, so integrating it would create a duplicate.
- `different`: unrelated or clearly distinct ("evaluation" vs "valuation").
- `related`: overlapping but not the same ("Risk Manager" vs "Risk
  Management", "Machine Learning" vs "Deep Learning"). Decide **before
  labelling** whether `related` counts as same or different for the
  calibration. Usually it counts as *different*, because merging related
  concepts loses information.

Write the labelling guide (with examples) first and keep it with the data.
Hide the scores from labellers so they aren't biased.

### 3. Check agreement

Have **two people** label independently and compute **Cohen's kappa** on
`label_a` vs `label_b`.

- κ ≥ 0.8: good. Resolve the disagreements into `label_final`.
- κ around 0.6–0.8: workable. Tighten the labelling guide and resolve.
- κ < 0.6: the task isn't well defined yet. Fix the guide before calibrating,
  because no threshold can beat the people labelling.

### 4. Split

Choose thresholds on **70%** of the labels. Hold back **30%** and don't look
at it until a threshold is chosen. Split separately within each level so
both parts cover every level.

### 5. Try every value

For each threshold, on the 70%, try every value in a range, e.g.
`semantic_threshold` from 0.50 to 0.95 in steps of 0.01. At each value:

- **precision** = of the pairs the rule calls "same", the share labelled `same`
- **recall** = of the pairs labelled `same`, the share the rule calls "same"

Mirror the pipeline's logic for each threshold. The `threshold` and
`semantic_threshold` decisions are joint: a close-spelling skip needs
**both**. Try them together (a 2-D grid), or fix one and vary the other.
Plot precision and recall against the threshold value.

### 6. Choose by a rule fixed in advance

Write the rule down **before** looking at the plot. Examples:

- `semantic_threshold`: the **lowest** value where skip precision ≥ **95%**.
  Recall is whatever that gives; misses go to review, not to silent skips.
- `semantic_threshold_by_level.specialities`: the same rule, on specialities
  pairs only.
- `add_candidate_*`: the **highest** values where the share of
  `ADD_CANDIDATE` pairs labelled `different` is ≥ 95%.

Write down the target precision (95%, 98%, …) and why you chose it. That's
the part people will question.

### 7. Report on the held-back 30%

Apply the chosen thresholds to the held-back pairs and report the precision
and recall there, with a confidence interval (e.g. a Wilson interval, since
the counts are small). This is the honest number; the 70% figure will look
better because it was used for tuning.

### 8. Record it

Next to the config (e.g. a comment block in the YAML, or a short note in this
folder), record:

- the labelled set's file name / version and sample seed
- the selection rule and target precision
- the chosen values
- the held-back precision and recall
- the semantic model name (`all-MiniLM-L6-v2`)

## Worked mini example

Semantic skips at the specialities level, on the 70%:

| `semantic_threshold` | Called "same" | Labelled `same` among them | Precision |
|---|---|---|---|
| 0.70 | 120 | 96 | 80% |
| 0.75 | 95 | 85 | 89% |
| 0.80 | 70 | 66 | 94% |
| 0.82 | 62 | 60 | **96.8%** |
| 0.85 | 48 | 47 | 97.9% |

Rule: lowest value with precision ≥ 95%, so choose **0.82**. Then check it on
the held-back 30%. If precision there is, say, 94% with an interval of
88–97%, report that, and consider whether the target or the sample size
needs to change.

## Which pairs calibrate which threshold

- **`threshold` + `semantic_threshold`:** pairs from `fuzzy_match_details`
  and `semantic_match_details`, plus `REVIEW_AMBIGUOUS` entries with
  `reason: close_spelling_unconfirmed` (close spelling that the meaning model
  rejected).
- **`semantic_threshold_by_level.<level>`:** the same pairs, restricted to that
  incoming level.
- **`add_candidate_*`:** `ambiguous_concepts`, both `ADD_CANDIDATE` and
  `REVIEW_AMBIGUOUS`, labelled against their closest names.
- **Parent check:** it uses the **global** `semantic_threshold`, not the
  per-level one (see `_uncovered_parents`). Calibrating it properly needs
  parent-vs-parent pairs, taken from `new_placement_details`
  (`unmatched_incoming_parents` vs `existing_parents`). Do this separately,
  or accept the concept-level value for now and note it.

## Later: learn the decision

Once a labelled set exists, replace five hand-set numbers with one learned
decision. Train a **logistic regression** on features such as:

- the spelling score (best, same level and other level)
- the meaning score (best, same level and other level)
- whether the match is at the same level
- the incoming level
- whether the parents match

It outputs a probability of "same concept". Pick **one** threshold on that
probability with the same precision rule, and keep a review band between the
skip and add cut-offs. It's usually more accurate and easier to justify, but
it needs the labels first, so calibration comes first either way. Validate it
on the same held-back set so you can compare it with the hand-set
thresholds.

## When to recalibrate

- When the semantic model changes (scores aren't comparable across models).
- When a very different kind of source is added (e.g. free-text gig requests
  instead of a curated framework).
- When the taxonomy schema or level meanings change.

After recalibrating, **re-run earlier comparisons** with the new values
before comparing curves. Mixing thresholds across runs invalidates the curve.

## Suggested tooling (future work)

The calibration can live in a notebook at first. If it becomes routine:

- **Sampler:** read `report.json`, draw a sample split by level and score
  with a fixed seed, and write the labelling CSV above (scores in separate
  columns, so they can be hidden from labellers).
- **Sweep:** read the labelled CSV, try every value for each threshold
  (mirroring `_winning_match` / `_ambiguous_entry`), plot precision and
  recall, apply the selection rule, and report held-back results with
  intervals.
- **Where:** a `calibration/` module under `src/taxonomy_pipeline/` with a
  `taxonomy-pipeline calibrate` CLI command, so the CLI and API share one
  code path, as `AGENTS.md` requires. Labelled data goes under a `data/` or
  `calibration/` folder that's checked in, so the chosen thresholds can be
  reproduced.

Each change needs tests and a README update in the same PR, per `AGENTS.md`.
