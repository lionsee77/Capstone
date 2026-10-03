# Saturation: options for deciding "good coverage"

Reference notes for future work, not a description of current behaviour. The
`saturation` metric (`src/taxonomy_pipeline/metrics/saturation.py`) produces a
new-items curve. These notes collect options for turning that curve into a
defensible answer to the question: *at what point does the taxonomy have
good coverage?*

## The problem

As more external sources are added, `new_rate` should fall, but it will
almost never reach 0. There is no universal "complete" number. Any cutoff is a
choice, and it becomes objective only if:

1. the rule is **fixed before** looking at the results, and
2. it is grounded in a method that others use and can check.

## What the pipeline gives us today

Each incoming source is one point in `details.curve`:

| Field | Meaning |
|---|---|
| `reviewed` | Unique concepts in this source that were compared |
| `add_candidates` | Confidently new (`ADD_CANDIDATE`) |
| `review_ambiguous` | Possibly new (`REVIEW_AMBIGUOUS`) |
| `new_rate_min` | `add_candidates / reviewed`, the floor |
| `new_rate_max` | `(add_candidates + review_ambiguous) / reviewed`, the ceiling |
| `new_placements`, `new_placement_rate` | Known concepts under a new parent (not counted as new) |
| `review_other_level` | Known concepts matched at another level (not counted as new) |
| `cumulative_known_concepts` | Size of the known pool after this source |

The metric returns data points only. Plotting is up to the consumer (a
notebook, CSV export, or the future dashboard reading run history from
SQLite).

### Caveats that apply to every option below

- **The pool grows by the whole source, not by what was accepted.**
  `_KnownPool.add` adds every concept from source *k*, including unapproved
  `ADD_CANDIDATE` / `REVIEW_AMBIGUOUS` items. The curve measures newness
  against *everything seen*, not against the curated taxonomy.
- **`cumulative_known_concepts` counts distinct match keys per level.** A
  concept matched only by close spelling or meaning, or matched at another
  level, still adds its own key. So the count overstates distinct concepts.
  Keep this in mind before using it as the y-axis of an accumulation curve.
- **Order matters.** Putting a broad source first makes later narrow ones look
  saturated, and vice versa.
- **A rate isn't a count.** `new_rate` is a share of each source's own size.
  A small source at 20% can add fewer concepts than a large one at 5%. Read
  `add_candidates` and `cumulative_known_concepts` alongside it.
- **The min–max band can be wide.** If most of a source is ambiguous, the
  curve says little until those items are reviewed.
- **The thresholds drive the curve.** Looser thresholds produce a lower rate.
  Only compare runs with identical thresholds; each run records them in
  `details`.
- **A flat or rising curve is a finding, not a failure.** It usually means
  a source covers an area (e.g. an industry) the taxonomy doesn't yet serve.

## Options

### 1. Stopping rule on the rate

**Answers:** is it still worth adding sources?

From research on thematic saturation in qualitative studies (when to stop
adding interviews):

- **Guest, Namey & Chen (2020):** saturation is reached when new information
  is **≤ 5%** over a set number of consecutive steps, measured against what
  was already known. 0% is the strict version.
- **Francis et al. (2010):** after an initial batch, stop once **3
  consecutive** sources add nothing new.

Applied here:

> Coverage is acceptable when `new_rate_max ≤ 10%` for **3 consecutive**
> incoming sources, with thresholds held fixed.

The team chose 10% rather than the literature's 5% to allow for the
uncalibrated matcher. Tighten it towards 5% once the thresholds have been
calibrated (see [`saturation-threshold-calibration.md`](saturation-threshold-calibration.md)).
Use `new_rate_max` so ambiguous items count against you. Requiring several
sources in a row stops one convenient source from ending the process early.

- **Supported today:** yes, read straight from `details.curve`.
- **Weakness:** it depends on source choice and order. Three narrow sources
  in a row can meet it by accident.

### 2. Chao1 / Good–Turing coverage estimate

**Answers:** what share of everything that exists do we already have?

Borrowed from ecology (estimating unseen species). **Intuition:** count birds
in a forest over several days. If almost every species has been seen on many
days, you've probably found nearly all of them. If lots of species were seen
on **only one day**, there are clearly more out there. Concepts seen only
once are the signal.

For each concept, count how many sources contain it. Let:

- `S_obs` = distinct concepts observed
- `n` = total concept sightings across all sources
- `f1` = concepts seen in **exactly one** source
- `f2` = concepts seen in **exactly two** sources

Then:

- **Good–Turing coverage:** `C ≈ 1 − f1 / n`. This is roughly the chance
  that a concept from the next source is already known.
- **Chao1 estimated total:** `S_est ≈ S_obs + f1² / (2 · f2)`, so coverage ≈
  `S_obs / S_est`.

**Worked example:** 1,000 concepts seen, 50 in one source, 100 in two:

```
unseen ≈ 50² / (2 × 100) = 12.5
S_est  ≈ 1,012.5
coverage ≈ 1,000 / 1,012.5 ≈ 98.8%
```

Same 1,000 concepts, but 300 in one source and 100 in two:

```
unseen ≈ 300² / (2 × 100) = 450
S_est  ≈ 1,450
coverage ≈ 69%
```

- **Suggested rule:** estimated coverage ≥ **90%** (95% if the matcher has
  been calibrated).
- **Supported today:** no. `_KnownPool` keeps only the *first* source of each
  concept, not how many sources contain it.
- **Weakness:** unreliable with few sources. It becomes meaningful at around
  4 or more.

### 3. Elbow point

**Answers:** when did adding sources stop paying off?

Plot an accumulation curve. It rises steeply, then flattens, and the
**elbow** is where it bends. Find it objectively, not by eye:

- **Furthest point from the straight line:** draw a line from the first point
  to the last, and pick the point furthest from it.
- **Kneedle** (Satopää et al., 2011): a published, more robust version of the
  same idea. Python's `kneed` package implements it.

Use a size-aware x-axis, because sources differ hugely in size:

- **x** = running total of `reviewed` across curve points
- **y** = `cumulative_known_concepts` (but see the match-key caveat above)

- **Supported today:** the data is there; it only needs plotting.
- **Weaknesses:**
  - It's *relative*. A curve can bend and still sit at a 20% new rate.
  - It needs around 5 or more points; with 2–3, any curve has a "bend".
  - It depends on source order.

### 4. Fitted curve ceiling

**Answers:** how many concepts exist in total, and how much more data would
reach X%?

Fit a curve that rises and then levels off to the accumulation curve, e.g.
`y = a · x / (b + x)` or `y = a · (1 − e^(−b·x))`. The fitted `a` is the
level the curve is heading towards, so coverage ≈ observed / `a`. The fit can
also predict how much more source data would reach a target coverage.

- **Supported today:** the data is there; it needs a fitting step.
- **Weakness:** needs around 5 or more sources to fit reliably, and the
  answer depends on which curve shape you pick.

### 5. Capture–recapture

**Answers:** a rough total-size estimate with just two sources.

From wildlife counting: catch and tag animals, release them, catch again,
and see how many are already tagged.

```
estimated total ≈ (concepts in A × concepts in B) / concepts in both
```

**Example:** original 1,000, SkillsFuture 2,000, overlap 400 →
total ≈ 5,000, so the original covers ~20%.

- **Supported today:** mostly.
  - Concepts in B = step 1 `reviewed`.
  - Overlap ≈ `reviewed − add_candidates − review_ambiguous` (any match:
    skips, new placements, other-level).
  - Concepts in A (distinct baseline concepts) isn't reported directly and
    would need counting or adding to `details`.
- **Weakness:** it assumes both sources sample the same field independently.
  That doesn't fully hold here: the original lists skills, SkillsFuture
  lists job roles and skills, and both are Singapore-focused. Treat the
  result as a rough figure.

### 6. Order-averaged curves (rarefaction)

**Answers:** how much of the curve's shape is just source order?

Recompute the curve under many random orderings of the incoming sources and
average them, which gives a mean curve with a band. This removes order
dependence from options 1, 3 and 4. It's cheap: the pairwise matching scores
can be computed once and reused across orders.

- **Supported today:** no. `incoming` is processed in config order only.

### 7. Per-sector coverage

**Answers:** *where* is coverage weak?

One overall number hides gaps: Banking might be at 98% while Healthcare is
at 60%. Running any of the above per category or service tells the team
where to source data next.

- **Supported today:** no. The curve is aggregated across the whole taxonomy.

### 8. Demand coverage

**Answers:** does the taxonomy cover what users actually need?

Sample real demand (job postings, Senseigigs gig requests, platform search
queries) and measure the share that maps to an existing concept, e.g. "92%
of real requests are covered". A variant is the platform's search
no-results rate.

Options 1–7 measure coverage of what the *sources* contain. This one measures
coverage of what *users* need, which is arguably what matters most for the
product.

- **Supported today:** no demand data is loaded. It could reuse the
  saturation matcher with the demand sample as an incoming source.

### 9. Domain-expert list

**Answers:** an independent sanity check.

Ask experts to list the essential skills for a few sectors, then check how
many are in the taxonomy. It's small and subjective, but independent of
every source.

## Comparison

| Option | Question it answers | Output | Usable with 2 sources? |
|---|---|---|---|
| 1. Stopping rule | Still worth adding sources? | Yes / no | No (needs 3+ incoming) |
| 2. Chao1 / Good–Turing | How much do we have? | "~93% coverage" | No (needs 4+) |
| 3. Elbow point | When did returns drop? | "After source 4" | No (needs 5+) |
| 4. Fitted ceiling | Total size; data needed for X% | Estimated total | No (needs 5+) |
| 5. Capture–recapture | Rough total size | Estimated total | **Yes** |
| 6. Order-averaged curves | Is the curve shape real? | Mean curve with band | Improves 1, 3, 4 |
| 7. Per-sector | Where are the gaps? | Coverage per sector | Improves any |
| 8. Demand coverage | Do we cover real needs? | "% of requests covered" | **Yes** |
| 9. Expert list | Independent check | Recall on a small list | **Yes** |

## Recommendation

| When | Use |
|---|---|
| Now (original + SkillsFuture) | Capture–recapture (5) for a rough first estimate, plus demand coverage (8) if job posting or search data is available |
| 5+ sources | Chao1 (2) or fitted ceiling (4) for the coverage estimate, on order-averaged curves (6), split by sector (7) |
| Report conclusion | Elbow (3) + stopping rule (1) + coverage estimate (2 or 4), with demand coverage (8) as the real-world check |

Example report sentence:

> "Returns diminished after source 4 (elbow). The last 3 sources each added
> ≤ 10% new concepts (stopping rule). Estimated coverage is ~93% (Chao1), and
> 91% of sampled gig requests map to an existing concept (demand coverage)."

## Preconditions

Without these, any coverage number should be reported as provisional:

1. **Thresholds calibrated** against a small hand-labelled set of concept
   pairs (see [`saturation-threshold-calibration.md`](saturation-threshold-calibration.md)).
2. **Thresholds fixed** across every run being compared.
3. **Ambiguous items reviewed**, or `new_rate_max` used throughout.
4. **Sources from the same field.** All of these methods measure coverage of
   the space the sources sample. If every source is Singapore-focused,
   95% means 95% of that space. The `breadth` metric (SSIC/SSOC) complements
   this, because it checks against an independent reference.

## Suggested pipeline changes (future work)

All in `src/taxonomy_pipeline/metrics/saturation.py` unless noted:

- **Per-concept source counts** (unlocks option 2): track, per known
  concept, the set of sources it appeared in (including via fuzzy/semantic
  matches), and emit `f1`, `f2`, `S_obs`, the Good–Turing estimate and Chao1
  per curve point.
- **Report distinct baseline concepts** in `details` (helps option 5).
- **Order permutation** (option 6): an optional `permutations: N` param that
  re-runs the step loop over shuffled `incoming` orders, reusing pairwise
  scores, and reports the mean curve and a band.
- **Per-sector breakdown** (option 7): group verdicts by each concept's
  category or service, and emit a curve per group.
- **Plotting / elbow / curve fit** (options 3, 4): keep these out of the
  metric. They belong in whatever consumes `details.curve` (a notebook or
  the dashboard), so the metric stays a data producer.

Each change needs tests and a README update in the same PR, per `AGENTS.md`.

## References

- Guest, G., Namey, E., & Chen, M. (2020). A simple method to assess and
  report thematic saturation in qualitative research. *PLOS ONE*, 15(5).
- Francis, J. J., et al. (2010). What is an adequate sample size?
  Operationalising data saturation for theory-based interview studies.
  *Psychology & Health*, 25(10).
- Satopää, V., Albrecht, J., Irwin, D., & Raghavan, B. (2011). Finding a
  "Kneedle" in a haystack: detecting knee points in system behavior.
  *ICDCS Workshops*.
- Chao, A. (1984). Nonparametric estimation of the number of classes in a
  population. *Scandinavian Journal of Statistics*, 11(4).
- Good, I. J. (1953). The population frequencies of species and the
  estimation of population parameters. *Biometrika*, 40(3–4).
