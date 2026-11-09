# Does the pipeline generalise past SkillsFuture? (O*NET, ESCO)

Issue #101. SkillsFuture is the only external source the pipeline was built
and tuned on. That covers the lexical rules in `matching/names.py`, breadth's
matching, saturation's thresholds and Case A/B placement. This note runs two
independent, officially maintained taxonomies through the same metrics,
unchanged, and records what holds and what doesn't:

- **O*NET**: the US occupation database, as imported by #119 (`backend/onet/`, hot technologies only).
- **ESCO**: the European Commission's occupations and skills classification (`backend/esco/`).

## How the numbers were produced

All numbers come from one run of `configs/generalization_v1.yaml`. It uses
**metric definitions v1**, the same settings as the baseline of record
(`configs/baseline_v1.yaml`), with no LLM judge:

```bash
taxonomy-pipeline run --config configs/generalization_v1.yaml --output generalization_v1.json
```

Definitions v1 pin the sentence model's revision, so rerunning gives the same
numbers. Saturation compares each source against the original taxonomy plus
every source before it, in the order SkillsFuture → O*NET → ESCO.

| Source | Categories | Specialities | Tags | Links |
|---|---|---|---|---|
| original | 110 | 593 | 676 | 1,439 |
| SkillsFuture | 41 | 1,948 | 2,387 | 43,346 |
| O*NET (#119, hot technologies) | 22 | 904 | 176 | 11,571 |
| ESCO (essential links) | 10 | 3,039 | 11,378 | 67,600 |

## 1. Adding a source: works without pipeline changes

Neither source needed a change to the pipeline itself:
- O*NET is already one row per occupation-software link, so `import-source` reads it directly.
- ESCO is four files joined by URI. `backend/esco/flatten_esco.py` (about 150 lines, tested) joins them into one table, and the same importer does the rest.

Every metric then ran on both dumps as-is.

Two quirks in ESCO's own export needed handling in the join:
- v1.2.1 repeats 4 occupation rows and 21 skill rows verbatim. The script drops exact repeats and stops on a conflicting one.
- Each link is marked `essential` or `optional`, and only essential links are kept.

The importer can't yet do either of these itself (#118).

## 2. Integrity: structural checks reflect how a source was imported

| | original | SkillsFuture | O*NET | ESCO |
|---|---|---|---|---|
| rows in exact-duplicate clusters | 53 | 114 | 0 | 0 |
| fuzzy pairs (≥ 85): specialities / tags | 28 / 57 | 695 / 310 | 72 / 1 | 597 / 962 |
| unused tags | 1 | 339 | 0 | 0 |
| dead-end categories / specialities | 14 / 1 | 2 / 0 | 0 / 0 | 0 / 0 |

**Zero unused tags and zero dead ends tell you how the source was imported, not how good it is.**
- `import-source` builds the tables from links, so every tag and speciality it creates has a link by construction.
- Under the earlier hand-written loaders (branch `feat/onet-generalization-testing`), the same sources showed 93 dead-end O*NET occupations and 2,561 unused ESCO skills.
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

**The 0% industry figures are fixed by the mapping, not found by matching.** Definitions v1 count only categories under the Industry service (`industry_service_id: 1`), and both mappings put every category under Function. A side check outside v1 counted their own categories as industry candidates (`industry_service_id: 2`):
- **O*NET: 12/82 (15%).** Some SOC major groups (e.g. *Legal*, *Construction and Extraction*) name fields of work.
- **ESCO: 0/82.** ISCO major groups are skill levels (*Professionals*, *Managers*, *Elementary occupations*), not fields.

So the industry axis depends on what a source's categories mean. For a source like O*NET, whose categories are partly industries, the service it's mapped to decides whether that coverage counts.

## 5. Saturation: a wide review band on every source, SkillsFuture included

How much of each source the thresholds can settle, against everything before it:

| | concepts | confidently new (add candidates) | needs review | confidently known |
|---|---|---|---|---|
| SkillsFuture | 4,308 | 665 (15.4%) | 2,934 (68.1%) | 709 (16.5%) |
| O*NET | 1,102 | 140 (12.7%) | 845 (76.7%) | 117 (10.6%) |
| ESCO | 14,424 | 1,217 (8.4%) | 9,912 (68.7%) | 3,295 (22.8%) |

By level, the share not confidently known (needs review or new):

| | specialities | tags |
|---|---|---|
| SkillsFuture | 96.5% | 73.9% |
| O*NET | 90.4% | 89.2% |
| ESCO | 77.3% | 77.1% |

**The thresholds send most concepts to review on every source, including SkillsFuture itself, the source they were set on.**
- ESCO's review share (68.7%) is the same as SkillsFuture's (68.1%).
- O*NET's is higher (76.7%), driven by its tags. These are software product names (*Microsoft SQL Server Reporting Services*), which a skills taxonomy rarely holds, so few match confidently either way.

This **corrects the earlier note** on `feat/onet-generalization-testing` and the #118 comment. Those said O*NET and ESCO showed far higher ambiguous rates than SkillsFuture, so the thresholds looked overfit to SkillsFuture. Measured the same way under v1, the review band is wide everywhere. So the problem isn't that the thresholds are tuned to SkillsFuture. They are uncalibrated, and they leave about two-thirds of any source to review or the LLM judge.

ESCO also has 613 occupations whose name matches a known concept under a different parent (96.5% of the placements checked). This is expected when one occupation title appears in several ISCO groups and in SkillsFuture's sectors.

## What this means

1. **Adding a source is cheap.** A linked-file source needs a short join script and a mapping. Nothing in the pipeline changes.
2. **Integrity's unused-tag and dead-end counts, and depth, describe a source's shape.** Use them to compare versions of the same taxonomy (baseline vs enriched), not to rank sources.
3. **Read fuzzy-duplicate counts with the pairs in hand.** At threshold 85 they overcount on long, curated names. Factor this into the #88 integrity guard rail.
4. **Whether a source's categories count toward industry coverage is set by its `service_id` in the mapping.** Choose it on purpose.
5. **Saturation needs calibration (#47), not source-specific tuning.** The wide review band appears on all three external sources. With SkillsFuture, O*NET and ESCO there are now three sources, the minimum #47 asks for, to calibrate against labelled pairs.

## Limitations

- ESCO was imported from a trimmed copy of the v1.2.1 export: essential links only, no skill descriptions (see `backend/esco/README.md`). O*NET is #119's hot-technology slice (176 of 8,753 software).
- Breadth's 0.60 cut-off and saturation's thresholds are provisional and uncalibrated (#47).
- No LLM judge was used. With a judge, part of every review band would be settled, so the review shares above are an upper bound for a judged round.
- Neither source has been through Case A/B or integrated. This note covers scoring and matching, not placement.
