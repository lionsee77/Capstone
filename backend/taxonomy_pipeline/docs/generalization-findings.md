# Does the pipeline generalise past SkillsFuture? (O*NET, ESCO)

Issue #101. SkillsFuture is the only external source the pipeline was built
and tuned on. That covers the lexical rules in `matching/names.py`, breadth's
matching, saturation's thresholds and Case A/B placement. This note runs two
independent, officially maintained taxonomies through the same metrics,
unchanged, and records what holds and what doesn't:

- **O*NET**: the US occupation database, as imported by #119 (`backend/onet/`, hot technologies only).
- **ESCO**: the European Commission's occupations and skills classification (`backend/esco/`).

## How the numbers were produced

Every number in this note comes from one run of
`configs/generalization_v1.yaml`, except the two old-branch counts in section
2, which are marked as such. The config uses **metric definitions v1**, the
same settings as the baseline of record (`configs/baseline_v1.yaml`), with no
LLM judge. It adds two kinds of entries outside v1, and each is labelled in
the config: an industry side check (section 4), and saturation of each source
against the original alone (section 5).

```bash
taxonomy-pipeline run --config configs/generalization_v1.yaml --output generalization_v1.json  # ~60 MB, gitignored
```

Definitions v1 pin the sentence model's revision, so rerunning gives the same
numbers. Saturation runs two ways:
- **Cumulative:** each source is compared against the original plus every source before it, in the order SkillsFuture → O*NET → ESCO.
- **Like-for-like:** each source is compared against the original alone.

Section 5 explains why both are needed.

| Source | Categories | Specialities | Tags | Links |
|---|---|---|---|---|
| original | 110 | 593 | 676 | 1,439 |
| SkillsFuture | 41 | 1,948 | 2,387 | 43,346 |
| O*NET (#119, hot technologies) | 22 | 904 | 176 | 11,571 |
| ESCO (essential links) | 10 | 3,039 | 11,378 | 67,600 |

## 1. Adding a source: works without pipeline changes

Neither source needed a change to the pipeline itself:
- O*NET is already one row per occupation-software link, so `import-source` reads it directly.
- ESCO is three files joined by URI. `backend/esco/flatten_esco.py` (about 220 lines including its docstrings, tested in `tests/test_flatten_esco.py`) joins them into one table, and the same importer does the rest.

Every metric then ran on both dumps as-is.

Two quirks in ESCO's export needed handling:
- **Repeated rows.** In the copy used here, v1.2.1 repeats 4 occupation rows and 21 skill rows, each with identical values in the columns the join reads. The script drops these repeats and stops on a repeat whose values differ. The importer can't do this itself, because it reads one table and the repeats sit in the files being joined (#118).
- **Essential and optional links.** Each link is marked `essential` or `optional`. The script carries the type through as a `relation_type` column, and the mapping's ordinary `filters` keeps essential links only.

## 2. Integrity: structural checks reflect how a source was imported

| | original | SkillsFuture | O*NET | ESCO |
|---|---|---|---|---|
| rows in exact-duplicate clusters | 53 | 114 | 0 | 0 |
| fuzzy pairs (≥ 85): specialities / tags | 28 / 57 | 695 / 310 | 72 / 1 | 597 / 962 |
| unused tags | 1 | 339 | 0 | 0 |
| dead-end categories / specialities | 14 / 1 | 2 / 0 | 0 / 0 | 0 / 0 |

**Zero unused tags and zero dead ends tell you how the source was imported, not how good it is.**
- `import-source` builds the tables from rows, one row per link. Every speciality it creates therefore has a link, and so does every tag whose row names its category and speciality. A tag with an incomplete parent path is kept without a link (`build_flat_taxonomy`). Neither O*NET's nor ESCO's flat table has such rows, so both show zero.
- Under the earlier hand-written loaders (branch `feat/onet-generalization-testing`), the same sources showed 93 dead-end O*NET occupations and 2,561 unused ESCO skills. These two counts come from that branch's own runs. They can't be reproduced from this config.
- Don't compare these counts across sources imported different ways.

**The fuzzy-duplicate check mostly flags distinct concepts in curated sources.** ESCO's and O*NET's top pairs are real, different things:
- *meteorology technician* / *metrology technician*
- *supervise loading of cargo* / *supervise unloading of cargo*
- *provide advice … export restrictions* / *… import restrictions*
- *chemistry teachers, postsecondary* / *history teachers, postsecondary*

At a RapidFuzz threshold of 85, long names that differ by one word score as near-duplicates. This matters for the integrity guard rail drafted in #88 (fuzzy pairs per 100 concepts must not rise more than 10%). Adding external rows can raise that rate without adding any real duplicates, so a rise needs a look at the pairs before it counts as a failure.

## 3. Depth: not comparable across sources of different shape

| | original | SkillsFuture | O*NET | ESCO |
|---|---|---|---|---|
| specialities per category (mean) | 5.39 (n=110) | 47.51 (n=41) | 41.09 (n=22) | 303.9 (n=10) |
| tags per speciality (mean) | 2.43 (n=593) | 22.25 (n=1,948) | 12.8 (n=904) | 22.24 (n=3,039) |

ISCO-08 has only 10 major groups covering every occupation, so ESCO's top
level holds about 56 times as many specialities per category as the original taxonomy. Depth measures the
shape of a hierarchy, so use it to compare versions of one taxonomy, not to
rank different sources.

## 4. Breadth: occupational coverage generalises; the industry figure depends on the mapping

| | original | SkillsFuture | O*NET | ESCO |
|---|---|---|---|---|
| occupational coverage (SSOC 2024 minor groups) | 16/145 (11%) | 84/145 (58%) | 129/145 (89%) | 127/145 (88%) |
| industry coverage (SSIC 2025 divisions), v1 | 18/82 (22%) | 20/82 (24%) | 0/82 (0%) | 0/82 (0%) |

**Occupational coverage gives the expected answer on both sources.** O*NET and ESCO are both built around occupations, so their specialities match most SSOC minor groups. Semantic matching against a reference behaves sensibly on naming styles it wasn't tuned on.

**The 0% industry figures are fixed by the mapping, not found by matching.** Definitions v1 count only categories under the Industry service (`industry_service_id: 1`), and both mappings put every category under Function. A side check outside v1 (the two `industry_service_id: 2` breadth entries in the config) counted their own categories as industry candidates:
- **O*NET: 12/82 (15%).** Some SOC major groups (e.g. *Legal*, *Construction and Extraction*) name fields of work.
- **ESCO: 0/82.** ISCO major groups are skill levels (*Professionals*, *Managers*, *Elementary occupations*), not fields.

So the industry axis depends on what a source's categories mean. For a source like O*NET, whose categories are partly industries, the service it's mapped to decides whether that coverage counts.

## 5. Saturation: compare sources against the same baseline, not along the cumulative curve

The cumulative curve compares each source with a different pool of known
concepts. `cumulative_known_concepts` grows from 1,310 before SkillsFuture to
5,618 before O*NET and 6,706 before ESCO. A bigger pool alone moves concepts
between add-candidate, review and known, so the curve can't show whether the
thresholds behave the same on every source. Comparing each source against the
original alone can.

Shares come from `recommended_action_counts`:
- **add** is `ADD_CANDIDATE`.
- **review** is `REVIEW_AMBIGUOUS`, `REVIEW_NEW_PLACEMENT` and `REVIEW_OTHER_LEVEL`.
- **known** is `SKIP_DUPLICATE` and `SKIP_SEMANTIC_DUPLICATE`.

**Like-for-like, each source against the original alone:**

| | concepts | add | review | known |
|---|---|---|---|---|
| SkillsFuture | 4,308 | 665 (15.4%) | 2,996 (69.5%) | 647 (15.0%) |
| O*NET | 1,102 | 420 (38.1%) | 651 (59.1%) | 31 (2.8%) |
| ESCO | 14,424 | 6,013 (41.7%) | 7,581 (52.6%) | 830 (5.8%) |

Review share by level, like-for-like:

| | categories | specialities | tags |
|---|---|---|---|
| SkillsFuture | 48.8% | 83.3% | 59.1% |
| O*NET | 59.1% | 62.9% | 39.2% |
| ESCO | 70.0% | 70.7% | 47.7% |

**Cumulative, each source against the original plus every earlier source:**

| | concepts | known concepts compared against | add | review | known |
|---|---|---|---|---|---|
| SkillsFuture | 4,308 | 1,310 | 665 (15.4%) | 2,996 (69.5%) | 647 (15.0%) |
| O*NET | 1,102 | 5,618 | 140 (12.7%) | 923 (83.8%) | 39 (3.5%) |
| ESCO | 14,424 | 6,706 | 1,217 (8.4%) | 10,581 (73.4%) | 2,626 (18.2%) |

What the two comparisons show:
- **Against the same baseline, neither unseen source is sent to review more often than SkillsFuture.** The review shares are SkillsFuture 69.5%, O*NET 59.1% and ESCO 52.6%. Most of the difference goes to add candidates instead. That is expected, because O*NET and ESCO overlap the original less than SkillsFuture does: only 2.8% and 5.8% of their concepts match a known one confidently.
- **The cumulative curve's higher review shares come from the larger pool.** ESCO's review share rises from 52.6% to 73.4%, and O*NET's from 59.1% to 83.8%, once SkillsFuture's concepts are in the pool. Each new source then has more near neighbours that score between the add and known thresholds. Expect every later round to send a larger share to review, whatever the source.
- **At every level, against every pool, roughly half or more of each source goes to review.** The one exception is O*NET's tags (39.2%).
- **On the cumulative curve, O*NET's review share is driven by its specialities, not its tags.** Specialities are 904 of its 1,102 concepts. 746 of them (82.5%) are `REVIEW_AMBIGUOUS`, which is 88% of its 845 ambiguous items. Only 88 of its 176 tags (50%) are. The occupation-title matches land between the thresholds, and that is the part of the source that matters for placement.

This **corrects the earlier note** on `feat/onet-generalization-testing` and the #118 comment. Those said O*NET and ESCO showed far higher ambiguous rates than SkillsFuture, so the thresholds looked overfit to SkillsFuture. That came from the cumulative comparison. Against the same baseline the unseen sources don't show higher review rates. That doesn't prove the thresholds fit them either. A review share says how often a score falls between two thresholds, not whether the concepts on either side are classified correctly. Only labelled pairs from each source can tell whether the thresholds are miscalibrated, and whether that differs by source (#47).

**ESCO's new-placement rate is most likely a side effect of the parent check, not a finding about placement. This is untested.**
- On the cumulative curve, 613 of 635 ESCO occupations that matched a known concept were flagged as a known concept in a new placement (96.5%).
- Repeated titles aren't the cause. All 3,039 names in `esco_specialities` are unique, even case-folded.
- The parent check compares category names. ESCO's categories are the ten ISCO major groups, which are skill levels (*Professionals*). The concepts they matched sit under sector or field names: O*NET's SOC groups (312 of the 613) and SkillsFuture's sectors (291). For example, *military engineer* (under *Armed forces occupations*) matched SkillsFuture's *Engineer* (under *Precision Engineering*, *Public Transport*). Such pairs almost never count as the same parent.
- The same thing shows up on the other sources. O*NET's rate is 87% (61 of 70) and SkillsFuture's 88% (30 of 34). Against the original alone, ESCO's is 25 of 25.

Read these rates as "the source's categories are built on a different axis". Don't carry them into Case A/B as evidence about where occupations belong.

## What this means

1. **Adding a source is cheap.** A linked-file source needs a short join script and a mapping. Nothing in the pipeline changes.
2. **Integrity's unused-tag and dead-end counts, and depth, describe a source's shape.** Use them to compare versions of the same taxonomy (baseline vs enriched), not to rank sources.
3. **Read fuzzy-duplicate counts with the pairs in hand.** At threshold 85 they overcount on long, curated names. Factor this into the #88 integrity guard rail.
4. **Whether a source's categories count toward industry coverage is set by its `service_id` in the mapping.** Choose it on purpose.
5. **Compare saturation across sources against one baseline, and calibrate it on labelled pairs (#47).** Against the original alone, the unseen sources aren't sent to review more often than SkillsFuture. The cumulative curve's higher shares come from its growing pool. The review band is still wide on all three, and it widens as the taxonomy grows. Whether the thresholds misclassify, and whether by source, needs labelled pairs. SkillsFuture, O*NET and ESCO are the three sources #47 asks for.
6. **Don't read the new-placement rate across sources whose categories are built on different axes.** It mostly reflects the parent check comparing skill levels with sectors.

## Limitations

- ESCO was imported from a trimmed copy of the v1.2.1 export: essential links only, no skill descriptions (see `backend/esco/README.md`). A full download hasn't been run through the script. O*NET is #119's hot-technology slice (176 of 8,753 software).
- The like-for-like comparison holds the baseline fixed, but not how much each source overlaps it. Lower review shares on O*NET and ESCO partly reflect that they share less with the original.
- Breadth's 0.60 cut-off and saturation's thresholds are provisional and uncalibrated (#47).
- No LLM judge was used. With a judge, part of every review band would be settled, so the review shares above are an upper bound for a judged round.
- Neither source has been through Case A/B or integrated. This note covers scoring and matching, not placement.
