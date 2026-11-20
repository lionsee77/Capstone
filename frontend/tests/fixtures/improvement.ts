/** Real API output (trimmed): `GET /lineage`, the judged lineage run, and its stage comparisons. */

export const lineage = {
  "stages": [
    {
      "id": "baseline:original",
      "name": "original",
      "parent": null,
      "sources": [],
      "created_at": null,
      "added": {}
    },
    {
      "id": "test-1015",
      "name": "test-1015",
      "parent": "baseline:original",
      "sources": [
        "skillsfuture"
      ],
      "created_at": "2026-10-15T04:13:01.805848Z",
      "added": {
        "categories": 28,
        "specialities": 556,
        "tags": 675,
        "speciality_tags": 676
      }
    },
    {
      "id": "judged-1015",
      "name": "judged-1015",
      "parent": "baseline:original",
      "sources": [
        "skillsfuture"
      ],
      "created_at": "2026-10-15T10:08:51.876562Z",
      "added": {
        "categories": 39,
        "specialities": 953,
        "tags": 1248,
        "speciality_tags": 1291
      }
    },
    {
      "id": "onet-1031",
      "name": "onet-1031",
      "parent": "judged-1015",
      "sources": [
        "onet"
      ],
      "created_at": "2026-10-31T13:42:21.575817Z",
      "added": {
        "categories": 0,
        "specialities": 147,
        "tags": 144,
        "speciality_tags": 148
      }
    }
  ],
  "chains": [
    {
      "stages": [
        "baseline:original",
        "judged-1015",
        "onet-1031"
      ],
      "runs": [
        "20428471e5af436b93faa39d1f25e472",
        "ffb9b0fd600e44f6b7d3843c52b6faf3"
      ]
    },
    {
      "stages": [
        "baseline:original",
        "test-1015"
      ],
      "runs": []
    }
  ],
  "runs": [
    {
      "run_id": "20428471e5af436b93faa39d1f25e472",
      "created_at": "2026-11-03T09:53:23.981084+00:00",
      "judged": true,
      "datasets": {
        "baseline:original": "stage0_original",
        "judged-1015": "stage1_skillsfuture",
        "onet-1031": "stage2_onet"
      }
    },
    {
      "run_id": "5bd9e578dfe7484f9d08e330a517d748",
      "created_at": "2026-11-03T09:25:20.879217+00:00",
      "judged": true,
      "datasets": {
        "onet-1031": "enriched"
      }
    },
    {
      "run_id": "ffb9b0fd600e44f6b7d3843c52b6faf3",
      "created_at": "2026-10-31T13:42:40.500347+00:00",
      "judged": false,
      "datasets": {
        "baseline:original": "stage0_original",
        "judged-1015": "stage1_skillsfuture",
        "onet-1031": "stage2_onet"
      }
    },
    {
      "run_id": "83abf111782440b0b814ef21e49bb909",
      "created_at": "2026-10-31T09:47:04.400427+00:00",
      "judged": true,
      "datasets": {
        "judged-1015": "round_baseline"
      }
    },
    {
      "run_id": "c8647e5d6cbc4c589ce082e07d7dc831",
      "created_at": "2026-10-06T04:41:22.682530+00:00",
      "judged": false,
      "datasets": {
        "baseline:original": "original"
      }
    }
  ]
};

export const lineageRunReport = {
  "run_id": "20428471e5af436b93faa39d1f25e472",
  "created_at": "2026-11-03T09:53:23.981084Z",
  "finished_at": "2026-11-03T09:55:12.476416Z",
  "status": "done",
  "metric_definitions_version": 1,
  "datasets": [
    {
      "name": "stage0_original",
      "counts": {
        "services": 4,
        "categories": 110,
        "specialities": 593,
        "tags": 676,
        "speciality_tags": 1439
      }
    },
    {
      "name": "stage1_skillsfuture",
      "counts": {
        "services": 4,
        "categories": 149,
        "specialities": 1546,
        "tags": 1924,
        "speciality_tags": 2730
      }
    },
    {
      "name": "stage2_onet",
      "counts": {
        "services": 4,
        "categories": 149,
        "specialities": 1693,
        "tags": 2068,
        "speciality_tags": 2878
      }
    },
    {
      "name": "skillsfuture",
      "counts": {
        "services": 0,
        "categories": 41,
        "specialities": 1948,
        "tags": 2387,
        "speciality_tags": 43346
      }
    },
    {
      "name": "onet",
      "counts": {
        "services": 0,
        "categories": 22,
        "specialities": 904,
        "tags": 176,
        "speciality_tags": 11571
      }
    },
    {
      "name": "ssic",
      "counts": {
        "section": 22,
        "division": 82,
        "group": 212,
        "class": 390,
        "subclass": 988
      }
    },
    {
      "name": "ssoc",
      "counts": {
        "major_group": 10,
        "sub_major_group": 42,
        "minor_group": 145,
        "unit_group": 414,
        "occupation": 1006
      }
    }
  ],
  "results": [
    {
      "metric_id": "integrity",
      "status": "ok",
      "summary": "stage0_original: 53 rows in exact-duplicate clusters, 0 orphan specialities, 1 unused tags, 14 dead-end categories, 1 dead-end specialities.",
      "details": {},
      "index": 0,
      "params": {
        "dataset": "stage0_original",
        "fuzzy_threshold": 85
      }
    },
    {
      "metric_id": "depth",
      "status": "ok",
      "summary": "stage0_original: category->speciality mean 5.39 (n=110), speciality->tag mean 2.43 (n=593).",
      "details": {},
      "index": 1,
      "params": {
        "dataset": "stage0_original"
      }
    },
    {
      "metric_id": "breadth",
      "status": "ok",
      "summary": "stage0_original: estimated semantic coverage (uncalibrated) \u2014 industry: 18/82 (22%); occupational: 16/145 (11%)",
      "details": {},
      "index": 2,
      "params": {
        "target": "stage0_original",
        "ssic_reference": "ssic",
        "ssoc_reference": "ssoc",
        "industry_level": "division",
        "occupational_level": "minor_group",
        "industry_service_id": 1,
        "threshold": 75.0,
        "preprocessing": "typography_v1",
        "matching_method": "semantic",
        "semantic_threshold": 0.6,
        "top_k": 5
      }
    },
    {
      "metric_id": "saturation",
      "status": "ok",
      "summary": "skillsfuture against stage0_original: new-items rate 44.0%-83.5% of 4308 concepts (1895 add candidates, 1701 need review); 30 known concepts in a new placement, 32 known at another level. Judge (gpt-6-luna) settled 1233 of 1410 ambiguous concepts it was asked about (tags): 3 duplicates, 1230 new, 177 unsure, 0 failed.",
      "details": {},
      "index": 3,
      "params": {
        "baseline": "stage0_original",
        "incoming": [
          "skillsfuture"
        ],
        "threshold": 90,
        "semantic_model": "sentence-transformers/all-MiniLM-L6-v2",
        "semantic_model_revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
        "semantic_threshold": 0.7,
        "add_candidate_lexical_threshold": 60,
        "add_candidate_semantic_threshold": 0.5,
        "cross_level": true,
        "semantic_threshold_by_level": {
          "specialities": 0.8
        },
        "judge": {
          "model": "gpt-6-luna",
          "candidates": 5,
          "same_at_or_above": 0.8,
          "new_at_or_above": 0.8,
          "levels": [
            "tags"
          ],
          "cache": "judge_cache.json",
          "workers": 8
        }
      }
    },
    {
      "metric_id": "saturation",
      "status": "ok",
      "summary": "onet against stage0_original: new-items rate 43.8%-95.7% of 1102 concepts (483 add candidates, 572 need review); 8 known concepts in a new placement, 8 known at another level. Judge (gpt-6-luna) settled 63 of 69 ambiguous concepts it was asked about (tags): 0 duplicates, 63 new, 6 unsure, 0 failed.",
      "details": {},
      "index": 4,
      "params": {
        "baseline": "stage0_original",
        "incoming": [
          "onet"
        ],
        "threshold": 90,
        "semantic_model": "sentence-transformers/all-MiniLM-L6-v2",
        "semantic_model_revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
        "semantic_threshold": 0.7,
        "add_candidate_lexical_threshold": 60,
        "add_candidate_semantic_threshold": 0.5,
        "cross_level": true,
        "semantic_threshold_by_level": {
          "specialities": 0.8
        },
        "judge": {
          "model": "gpt-6-luna",
          "candidates": 5,
          "same_at_or_above": 0.8,
          "new_at_or_above": 0.8,
          "levels": [
            "tags"
          ],
          "cache": "judge_cache_onet.json",
          "workers": 8
        }
      }
    },
    {
      "metric_id": "integrity",
      "status": "ok",
      "summary": "stage1_skillsfuture: 238 rows in exact-duplicate clusters, 0 orphan specialities, 1 unused tags, 12 dead-end categories, 1 dead-end specialities.",
      "details": {},
      "index": 5,
      "params": {
        "dataset": "stage1_skillsfuture",
        "fuzzy_threshold": 85
      }
    },
    {
      "metric_id": "depth",
      "status": "ok",
      "summary": "stage1_skillsfuture: category->speciality mean 10.38 (n=149), speciality->tag mean 1.77 (n=1546).",
      "details": {},
      "index": 6,
      "params": {
        "dataset": "stage1_skillsfuture"
      }
    },
    {
      "metric_id": "breadth",
      "status": "ok",
      "summary": "stage1_skillsfuture: estimated semantic coverage (uncalibrated) \u2014 industry: 22/82 (27%); occupational: 42/145 (29%)",
      "details": {},
      "index": 7,
      "params": {
        "target": "stage1_skillsfuture",
        "ssic_reference": "ssic",
        "ssoc_reference": "ssoc",
        "industry_level": "division",
        "occupational_level": "minor_group",
        "industry_service_id": 1,
        "threshold": 75.0,
        "preprocessing": "typography_v1",
        "matching_method": "semantic",
        "semantic_threshold": 0.6,
        "top_k": 5
      }
    },
    {
      "metric_id": "saturation",
      "status": "ok",
      "summary": "skillsfuture against stage1_skillsfuture: new-items rate 9.2%-49.5% of 4308 concepts (398 add candidates, 1734 need review); 47 known concepts in a new placement, 46 known at another level. Judge (gpt-6-luna) settled 292 of 343 ambiguous concepts it was asked about (tags): 4 duplicates, 288 new, 51 unsure, 0 failed.",
      "details": {},
      "index": 8,
      "params": {
        "baseline": "stage1_skillsfuture",
        "incoming": [
          "skillsfuture"
        ],
        "threshold": 90,
        "semantic_model": "sentence-transformers/all-MiniLM-L6-v2",
        "semantic_model_revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
        "semantic_threshold": 0.7,
        "add_candidate_lexical_threshold": 60,
        "add_candidate_semantic_threshold": 0.5,
        "cross_level": true,
        "semantic_threshold_by_level": {
          "specialities": 0.8
        },
        "judge": {
          "model": "gpt-6-luna",
          "candidates": 5,
          "same_at_or_above": 0.8,
          "new_at_or_above": 0.8,
          "levels": [
            "tags"
          ],
          "cache": "judge_cache.json",
          "workers": 8
        }
      }
    },
    {
      "metric_id": "saturation",
      "status": "ok",
      "summary": "onet against stage1_skillsfuture: new-items rate 28.3%-95.1% of 1102 concepts (312 add candidates, 736 need review); 11 known concepts in a new placement, 9 known at another level. Judge (gpt-6-luna) settled 64 of 77 ambiguous concepts it was asked about (tags): 0 duplicates, 64 new, 13 unsure, 0 failed.",
      "details": {},
      "index": 9,
      "params": {
        "baseline": "stage1_skillsfuture",
        "incoming": [
          "onet"
        ],
        "threshold": 90,
        "semantic_model": "sentence-transformers/all-MiniLM-L6-v2",
        "semantic_model_revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
        "semantic_threshold": 0.7,
        "add_candidate_lexical_threshold": 60,
        "add_candidate_semantic_threshold": 0.5,
        "cross_level": true,
        "semantic_threshold_by_level": {
          "specialities": 0.8
        },
        "judge": {
          "model": "gpt-6-luna",
          "candidates": 5,
          "same_at_or_above": 0.8,
          "new_at_or_above": 0.8,
          "levels": [
            "tags"
          ],
          "cache": "judge_cache_onet.json",
          "workers": 8
        }
      }
    },
    {
      "metric_id": "integrity",
      "status": "ok",
      "summary": "stage2_onet: 244 rows in exact-duplicate clusters, 0 orphan specialities, 1 unused tags, 12 dead-end categories, 1 dead-end specialities.",
      "details": {},
      "index": 10,
      "params": {
        "dataset": "stage2_onet",
        "fuzzy_threshold": 85
      }
    },
    {
      "metric_id": "depth",
      "status": "ok",
      "summary": "stage2_onet: category->speciality mean 11.36 (n=149), speciality->tag mean 1.7 (n=1693).",
      "details": {},
      "index": 11,
      "params": {
        "dataset": "stage2_onet"
      }
    },
    {
      "metric_id": "breadth",
      "status": "ok",
      "summary": "stage2_onet: estimated semantic coverage (uncalibrated) \u2014 industry: 22/82 (27%); occupational: 43/145 (30%)",
      "details": {},
      "index": 12,
      "params": {
        "target": "stage2_onet",
        "ssic_reference": "ssic",
        "ssoc_reference": "ssoc",
        "industry_level": "division",
        "occupational_level": "minor_group",
        "industry_service_id": 1,
        "threshold": 75.0,
        "preprocessing": "typography_v1",
        "matching_method": "semantic",
        "semantic_threshold": 0.6,
        "top_k": 5
      }
    },
    {
      "metric_id": "saturation",
      "status": "ok",
      "summary": "skillsfuture against stage2_onet: new-items rate 9.1%-49.4% of 4308 concepts (393 add candidates, 1737 need review); 49 known concepts in a new placement, 46 known at another level. Judge (gpt-6-luna) settled 293 of 343 ambiguous concepts it was asked about (tags): 4 duplicates, 289 new, 50 unsure, 0 failed.",
      "details": {},
      "index": 13,
      "params": {
        "baseline": "stage2_onet",
        "incoming": [
          "skillsfuture"
        ],
        "threshold": 90,
        "semantic_model": "sentence-transformers/all-MiniLM-L6-v2",
        "semantic_model_revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
        "semantic_threshold": 0.7,
        "add_candidate_lexical_threshold": 60,
        "add_candidate_semantic_threshold": 0.5,
        "cross_level": true,
        "semantic_threshold_by_level": {
          "specialities": 0.8
        },
        "judge": {
          "model": "gpt-6-luna",
          "candidates": 5,
          "same_at_or_above": 0.8,
          "new_at_or_above": 0.8,
          "levels": [
            "tags"
          ],
          "cache": "judge_cache.json",
          "workers": 8
        }
      }
    },
    {
      "metric_id": "saturation",
      "status": "ok",
      "summary": "onet against stage2_onet: new-items rate 16.2%-81.9% of 1102 concepts (179 add candidates, 724 need review); 11 known concepts in a new placement, 9 known at another level. Judge (gpt-6-luna) settled 12 of 12 ambiguous concepts it was asked about (tags): 0 duplicates, 12 new, 0 unsure, 0 failed.",
      "details": {},
      "index": 14,
      "params": {
        "baseline": "stage2_onet",
        "incoming": [
          "onet"
        ],
        "threshold": 90,
        "semantic_model": "sentence-transformers/all-MiniLM-L6-v2",
        "semantic_model_revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
        "semantic_threshold": 0.7,
        "add_candidate_lexical_threshold": 60,
        "add_candidate_semantic_threshold": 0.5,
        "cross_level": true,
        "semantic_threshold_by_level": {
          "specialities": 0.8
        },
        "judge": {
          "model": "gpt-6-luna",
          "candidates": 5,
          "same_at_or_above": 0.8,
          "new_at_or_above": 0.8,
          "levels": [
            "tags"
          ],
          "cache": "judge_cache_onet.json",
          "workers": 8
        }
      }
    }
  ],
  "error": null
};

export const step1Comparison = {
  "comparability": {
    "before_run_id": "20428471e5af436b93faa39d1f25e472",
    "after_run_id": "20428471e5af436b93faa39d1f25e472",
    "before_subject": "stage0_original",
    "after_subject": "stage1_skillsfuture",
    "before_definitions_version": 1,
    "after_definitions_version": 1,
    "issues": [],
    "metrics": [
      {
        "metric_id": "integrity",
        "before_index": 0,
        "after_index": 5,
        "issues": []
      },
      {
        "metric_id": "depth",
        "before_index": 1,
        "after_index": 6,
        "issues": []
      },
      {
        "metric_id": "breadth",
        "before_index": 2,
        "after_index": 7,
        "issues": []
      },
      {
        "metric_id": "saturation",
        "before_index": 3,
        "after_index": 8,
        "issues": []
      },
      {
        "metric_id": "saturation",
        "before_index": 4,
        "after_index": 9,
        "issues": []
      }
    ],
    "comparable": true
  },
  "metrics": [
    {
      "metric_id": "integrity",
      "label": "integrity (dataset=stage0_original)",
      "before_index": 0,
      "after_index": 5,
      "issues": [],
      "measures": [
        {
          "key": "exact_duplicate_rows.categories",
          "label": "Categories in exact-duplicate clusters",
          "better": "lower",
          "unit": "number",
          "before": 2.0,
          "after": 56.0,
          "change": 54.0,
          "relative_change": 27.0,
          "verdict": "deteriorated"
        },
        {
          "key": "exact_duplicate_rows.specialities",
          "label": "Specialities in exact-duplicate clusters",
          "better": "lower",
          "unit": "number",
          "before": 51.0,
          "after": 182.0,
          "change": 131.0,
          "relative_change": 2.5686274509803924,
          "verdict": "deteriorated"
        },
        {
          "key": "exact_duplicate_rows.tags",
          "label": "Tags in exact-duplicate clusters",
          "better": "lower",
          "unit": "number",
          "before": 0.0,
          "after": 0.0,
          "change": 0.0,
          "relative_change": null,
          "verdict": "unchanged"
        },
        {
          "key": "fuzzy_duplicate_pairs.categories",
          "label": "Fuzzy-duplicate category pairs",
          "better": "lower",
          "unit": "number",
          "before": 1.0,
          "after": 2.0,
          "change": 1.0,
          "relative_change": 1.0,
          "verdict": "deteriorated"
        },
        {
          "key": "fuzzy_duplicate_pairs.specialities",
          "label": "Fuzzy-duplicate speciality pairs",
          "better": "lower",
          "unit": "number",
          "before": 28.0,
          "after": 97.0,
          "change": 69.0,
          "relative_change": 2.4642857142857144,
          "verdict": "deteriorated"
        },
        {
          "key": "fuzzy_duplicate_pairs.tags",
          "label": "Fuzzy-duplicate tag pairs",
          "better": "lower",
          "unit": "number",
          "before": 57.0,
          "after": 131.0,
          "change": 74.0,
          "relative_change": 1.2982456140350878,
          "verdict": "deteriorated"
        },
        {
          "key": "orphans.categories_without_service",
          "label": "Categories without a service",
          "better": "lower",
          "unit": "number",
          "before": 0.0,
          "after": 0.0,
          "change": 0.0,
          "relative_change": null,
          "verdict": "unchanged"
        },
        {
          "key": "orphans.specialities_without_category",
          "label": "Specialities without a category",
          "better": "lower",
          "unit": "number",
          "before": 0.0,
          "after": 0.0,
          "change": 0.0,
          "relative_change": null,
          "verdict": "unchanged"
        },
        {
          "key": "orphans.tags_unused_by_any_speciality",
          "label": "Tags no speciality uses",
          "better": "lower",
          "unit": "number",
          "before": 1.0,
          "after": 1.0,
          "change": 0.0,
          "relative_change": 0.0,
          "verdict": "unchanged"
        },
        {
          "key": "orphans.dangling_speciality_tag_links",
          "label": "Dangling speciality-tag links",
          "better": "lower",
          "unit": "number",
          "before": 0.0,
          "after": 0.0,
          "change": 0.0,
          "relative_change": null,
          "verdict": "unchanged"
        },
        {
          "key": "dead_ends.categories_with_zero_specialities",
          "label": "Dead-end categories",
          "better": "lower",
          "unit": "number",
          "before": 14.0,
          "after": 12.0,
          "change": -2.0,
          "relative_change": -0.14285714285714285,
          "verdict": "improved"
        },
        {
          "key": "dead_ends.specialities_with_zero_tags",
          "label": "Dead-end specialities",
          "better": "lower",
          "unit": "number",
          "before": 1.0,
          "after": 1.0,
          "change": 0.0,
          "relative_change": 0.0,
          "verdict": "unchanged"
        },
        {
          "key": "dead_ends.category_dead_end_rate",
          "label": "Dead-end category rate",
          "better": "lower",
          "unit": "rate",
          "before": 0.12727272727272726,
          "after": 0.08053691275167785,
          "change": -0.04673581452104941,
          "relative_change": -0.36720997123681687,
          "verdict": "improved"
        },
        {
          "key": "dead_ends.speciality_dead_end_rate",
          "label": "Dead-end speciality rate",
          "better": "lower",
          "unit": "rate",
          "before": 0.0016863406408094434,
          "after": 0.000646830530401035,
          "change": -0.0010395101104084085,
          "relative_change": -0.6164294954721863,
          "verdict": "improved"
        }
      ]
    },
    {
      "metric_id": "depth",
      "label": "depth (dataset=stage0_original)",
      "before_index": 1,
      "after_index": 6,
      "issues": [],
      "measures": [
        {
          "key": "speciality_to_tag.mean",
          "label": "Tags per speciality (mean)",
          "better": "higher",
          "unit": "number",
          "before": 2.43,
          "after": 1.77,
          "change": -0.6600000000000001,
          "relative_change": -0.271604938271605,
          "verdict": "deteriorated"
        },
        {
          "key": "speciality_to_tag.median",
          "label": "Tags per speciality (median)",
          "better": "higher",
          "unit": "number",
          "before": 2.0,
          "after": 1.0,
          "change": -1.0,
          "relative_change": -0.5,
          "verdict": "deteriorated"
        },
        {
          "key": "category_to_speciality.mean",
          "label": "Specialities per category (mean)",
          "better": null,
          "unit": "number",
          "before": 5.39,
          "after": 10.38,
          "change": 4.990000000000001,
          "relative_change": 0.9257884972170689,
          "verdict": "changed"
        },
        {
          "key": "category_to_speciality.median",
          "label": "Specialities per category (median)",
          "better": null,
          "unit": "number",
          "before": 5.0,
          "after": 6.0,
          "change": 1.0,
          "relative_change": 0.2,
          "verdict": "changed"
        },
        {
          "key": "category_to_speciality.n",
          "label": "Categories",
          "better": null,
          "unit": "number",
          "before": 110.0,
          "after": 149.0,
          "change": 39.0,
          "relative_change": 0.35454545454545455,
          "verdict": "changed"
        },
        {
          "key": "speciality_to_tag.n",
          "label": "Specialities",
          "better": null,
          "unit": "number",
          "before": 593.0,
          "after": 1546.0,
          "change": 953.0,
          "relative_change": 1.6070826306913997,
          "verdict": "changed"
        }
      ]
    },
    {
      "metric_id": "breadth",
      "label": "breadth (target=stage0_original)",
      "before_index": 2,
      "after_index": 7,
      "issues": [],
      "measures": [
        {
          "key": "industry.matched_count",
          "label": "SSIC industry nodes covered",
          "better": "higher",
          "unit": "number",
          "before": 18.0,
          "after": 22.0,
          "change": 4.0,
          "relative_change": 0.2222222222222222,
          "verdict": "improved"
        },
        {
          "key": "industry.coverage",
          "label": "Industry coverage",
          "better": "higher",
          "unit": "rate",
          "before": 0.22,
          "after": 0.268,
          "change": 0.048000000000000015,
          "relative_change": 0.21818181818181825,
          "verdict": "improved"
        },
        {
          "key": "occupational.matched_count",
          "label": "SSOC occupational nodes covered",
          "better": "higher",
          "unit": "number",
          "before": 16.0,
          "after": 42.0,
          "change": 26.0,
          "relative_change": 1.625,
          "verdict": "improved"
        },
        {
          "key": "occupational.coverage",
          "label": "Occupational coverage",
          "better": "higher",
          "unit": "rate",
          "before": 0.11,
          "after": 0.29,
          "change": 0.18,
          "relative_change": 1.6363636363636362,
          "verdict": "improved"
        }
      ]
    },
    {
      "metric_id": "saturation",
      "label": "saturation (baseline=stage0_original, incoming=skillsfuture, judge={cache:judge_cache.json,candidates:5,levels:tags,model:gpt-6-luna,new_at_or_above:0.8,same_at_or_above:0.8,workers:8})",
      "before_index": 3,
      "after_index": 8,
      "issues": [],
      "measures": [
        {
          "key": "add_candidates",
          "label": "Add candidates",
          "better": "lower",
          "unit": "number",
          "before": 1895.0,
          "after": 398.0,
          "change": -1497.0,
          "relative_change": -0.7899736147757256,
          "verdict": "improved"
        },
        {
          "key": "review_ambiguous",
          "label": "Concepts needing review",
          "better": "lower",
          "unit": "number",
          "before": 1701.0,
          "after": 1734.0,
          "change": 33.0,
          "relative_change": 0.019400352733686066,
          "verdict": "deteriorated"
        },
        {
          "key": "new_rate_min",
          "label": "New-items rate (min)",
          "better": "lower",
          "unit": "rate",
          "before": 0.44,
          "after": 0.092,
          "change": -0.348,
          "relative_change": -0.7909090909090909,
          "verdict": "improved"
        },
        {
          "key": "new_rate_max",
          "label": "New-items rate (max)",
          "better": "lower",
          "unit": "rate",
          "before": 0.835,
          "after": 0.495,
          "change": -0.33999999999999997,
          "relative_change": -0.40718562874251496,
          "verdict": "improved"
        },
        {
          "key": "reviewed",
          "label": "Incoming concepts reviewed",
          "better": null,
          "unit": "number",
          "before": 4308.0,
          "after": 4308.0,
          "change": 0.0,
          "relative_change": 0.0,
          "verdict": "unchanged"
        },
        {
          "key": "new_placements",
          "label": "Known concepts in a new placement",
          "better": null,
          "unit": "number",
          "before": 30.0,
          "after": 47.0,
          "change": 17.0,
          "relative_change": 0.5666666666666667,
          "verdict": "changed"
        },
        {
          "key": "review_other_level",
          "label": "Known concepts at another level",
          "better": null,
          "unit": "number",
          "before": 32.0,
          "after": 46.0,
          "change": 14.0,
          "relative_change": 0.4375,
          "verdict": "changed"
        }
      ]
    },
    {
      "metric_id": "saturation",
      "label": "saturation (baseline=stage0_original, incoming=onet, judge={cache:judge_cache_onet.json,candidates:5,levels:tags,model:gpt-6-luna,new_at_or_above:0.8,same_at_or_above:0.8,workers:8})",
      "before_index": 4,
      "after_index": 9,
      "issues": [],
      "measures": [
        {
          "key": "add_candidates",
          "label": "Add candidates",
          "better": "lower",
          "unit": "number",
          "before": 483.0,
          "after": 312.0,
          "change": -171.0,
          "relative_change": -0.35403726708074534,
          "verdict": "improved"
        },
        {
          "key": "review_ambiguous",
          "label": "Concepts needing review",
          "better": "lower",
          "unit": "number",
          "before": 572.0,
          "after": 736.0,
          "change": 164.0,
          "relative_change": 0.2867132867132867,
          "verdict": "deteriorated"
        },
        {
          "key": "new_rate_min",
          "label": "New-items rate (min)",
          "better": "lower",
          "unit": "rate",
          "before": 0.438,
          "after": 0.283,
          "change": -0.15500000000000003,
          "relative_change": -0.35388127853881285,
          "verdict": "improved"
        },
        {
          "key": "new_rate_max",
          "label": "New-items rate (max)",
          "better": "lower",
          "unit": "rate",
          "before": 0.957,
          "after": 0.951,
          "change": -0.006000000000000005,
          "relative_change": -0.006269592476489034,
          "verdict": "improved"
        },
        {
          "key": "reviewed",
          "label": "Incoming concepts reviewed",
          "better": null,
          "unit": "number",
          "before": 1102.0,
          "after": 1102.0,
          "change": 0.0,
          "relative_change": 0.0,
          "verdict": "unchanged"
        },
        {
          "key": "new_placements",
          "label": "Known concepts in a new placement",
          "better": null,
          "unit": "number",
          "before": 8.0,
          "after": 11.0,
          "change": 3.0,
          "relative_change": 0.375,
          "verdict": "changed"
        },
        {
          "key": "review_other_level",
          "label": "Known concepts at another level",
          "better": null,
          "unit": "number",
          "before": 8.0,
          "after": 9.0,
          "change": 1.0,
          "relative_change": 0.125,
          "verdict": "changed"
        }
      ]
    }
  ],
  "comparable": true
};

export const step2Comparison = {
  "comparability": {
    "before_run_id": "20428471e5af436b93faa39d1f25e472",
    "after_run_id": "20428471e5af436b93faa39d1f25e472",
    "before_subject": "stage1_skillsfuture",
    "after_subject": "stage2_onet",
    "before_definitions_version": 1,
    "after_definitions_version": 1,
    "issues": [],
    "metrics": [
      {
        "metric_id": "integrity",
        "before_index": 5,
        "after_index": 10,
        "issues": []
      },
      {
        "metric_id": "depth",
        "before_index": 6,
        "after_index": 11,
        "issues": []
      },
      {
        "metric_id": "breadth",
        "before_index": 7,
        "after_index": 12,
        "issues": []
      },
      {
        "metric_id": "saturation",
        "before_index": 8,
        "after_index": 13,
        "issues": []
      },
      {
        "metric_id": "saturation",
        "before_index": 9,
        "after_index": 14,
        "issues": []
      }
    ],
    "comparable": true
  },
  "metrics": [
    {
      "metric_id": "integrity",
      "label": "integrity (dataset=stage1_skillsfuture)",
      "before_index": 5,
      "after_index": 10,
      "issues": [],
      "measures": [
        {
          "key": "exact_duplicate_rows.categories",
          "label": "Categories in exact-duplicate clusters",
          "better": "lower",
          "unit": "number",
          "before": 56.0,
          "after": 56.0,
          "change": 0.0,
          "relative_change": 0.0,
          "verdict": "unchanged"
        },
        {
          "key": "exact_duplicate_rows.specialities",
          "label": "Specialities in exact-duplicate clusters",
          "better": "lower",
          "unit": "number",
          "before": 182.0,
          "after": 188.0,
          "change": 6.0,
          "relative_change": 0.03296703296703297,
          "verdict": "deteriorated"
        },
        {
          "key": "exact_duplicate_rows.tags",
          "label": "Tags in exact-duplicate clusters",
          "better": "lower",
          "unit": "number",
          "before": 0.0,
          "after": 0.0,
          "change": 0.0,
          "relative_change": null,
          "verdict": "unchanged"
        },
        {
          "key": "fuzzy_duplicate_pairs.categories",
          "label": "Fuzzy-duplicate category pairs",
          "better": "lower",
          "unit": "number",
          "before": 2.0,
          "after": 2.0,
          "change": 0.0,
          "relative_change": 0.0,
          "verdict": "unchanged"
        },
        {
          "key": "fuzzy_duplicate_pairs.specialities",
          "label": "Fuzzy-duplicate speciality pairs",
          "better": "lower",
          "unit": "number",
          "before": 97.0,
          "after": 98.0,
          "change": 1.0,
          "relative_change": 0.010309278350515464,
          "verdict": "deteriorated"
        },
        {
          "key": "fuzzy_duplicate_pairs.tags",
          "label": "Fuzzy-duplicate tag pairs",
          "better": "lower",
          "unit": "number",
          "before": 131.0,
          "after": 132.0,
          "change": 1.0,
          "relative_change": 0.007633587786259542,
          "verdict": "deteriorated"
        },
        {
          "key": "orphans.categories_without_service",
          "label": "Categories without a service",
          "better": "lower",
          "unit": "number",
          "before": 0.0,
          "after": 0.0,
          "change": 0.0,
          "relative_change": null,
          "verdict": "unchanged"
        },
        {
          "key": "orphans.specialities_without_category",
          "label": "Specialities without a category",
          "better": "lower",
          "unit": "number",
          "before": 0.0,
          "after": 0.0,
          "change": 0.0,
          "relative_change": null,
          "verdict": "unchanged"
        },
        {
          "key": "orphans.tags_unused_by_any_speciality",
          "label": "Tags no speciality uses",
          "better": "lower",
          "unit": "number",
          "before": 1.0,
          "after": 1.0,
          "change": 0.0,
          "relative_change": 0.0,
          "verdict": "unchanged"
        },
        {
          "key": "orphans.dangling_speciality_tag_links",
          "label": "Dangling speciality-tag links",
          "better": "lower",
          "unit": "number",
          "before": 0.0,
          "after": 0.0,
          "change": 0.0,
          "relative_change": null,
          "verdict": "unchanged"
        },
        {
          "key": "dead_ends.categories_with_zero_specialities",
          "label": "Dead-end categories",
          "better": "lower",
          "unit": "number",
          "before": 12.0,
          "after": 12.0,
          "change": 0.0,
          "relative_change": 0.0,
          "verdict": "unchanged"
        },
        {
          "key": "dead_ends.specialities_with_zero_tags",
          "label": "Dead-end specialities",
          "better": "lower",
          "unit": "number",
          "before": 1.0,
          "after": 1.0,
          "change": 0.0,
          "relative_change": 0.0,
          "verdict": "unchanged"
        },
        {
          "key": "dead_ends.category_dead_end_rate",
          "label": "Dead-end category rate",
          "better": "lower",
          "unit": "rate",
          "before": 0.08053691275167785,
          "after": 0.08053691275167785,
          "change": 0.0,
          "relative_change": 0.0,
          "verdict": "unchanged"
        },
        {
          "key": "dead_ends.speciality_dead_end_rate",
          "label": "Dead-end speciality rate",
          "better": "lower",
          "unit": "rate",
          "before": 0.000646830530401035,
          "after": 0.0005906674542232723,
          "change": -0.00005616307617776269,
          "relative_change": -0.08682811577082111,
          "verdict": "improved"
        }
      ]
    },
    {
      "metric_id": "depth",
      "label": "depth (dataset=stage1_skillsfuture)",
      "before_index": 6,
      "after_index": 11,
      "issues": [],
      "measures": [
        {
          "key": "speciality_to_tag.mean",
          "label": "Tags per speciality (mean)",
          "better": "higher",
          "unit": "number",
          "before": 1.77,
          "after": 1.7,
          "change": -0.07000000000000006,
          "relative_change": -0.039548022598870095,
          "verdict": "deteriorated"
        },
        {
          "key": "speciality_to_tag.median",
          "label": "Tags per speciality (median)",
          "better": "higher",
          "unit": "number",
          "before": 1.0,
          "after": 1.0,
          "change": 0.0,
          "relative_change": 0.0,
          "verdict": "unchanged"
        },
        {
          "key": "category_to_speciality.mean",
          "label": "Specialities per category (mean)",
          "better": null,
          "unit": "number",
          "before": 10.38,
          "after": 11.36,
          "change": 0.9799999999999986,
          "relative_change": 0.09441233140655092,
          "verdict": "changed"
        },
        {
          "key": "category_to_speciality.median",
          "label": "Specialities per category (median)",
          "better": null,
          "unit": "number",
          "before": 6.0,
          "after": 6.0,
          "change": 0.0,
          "relative_change": 0.0,
          "verdict": "unchanged"
        },
        {
          "key": "category_to_speciality.n",
          "label": "Categories",
          "better": null,
          "unit": "number",
          "before": 149.0,
          "after": 149.0,
          "change": 0.0,
          "relative_change": 0.0,
          "verdict": "unchanged"
        },
        {
          "key": "speciality_to_tag.n",
          "label": "Specialities",
          "better": null,
          "unit": "number",
          "before": 1546.0,
          "after": 1693.0,
          "change": 147.0,
          "relative_change": 0.09508408796895214,
          "verdict": "changed"
        }
      ]
    },
    {
      "metric_id": "breadth",
      "label": "breadth (target=stage1_skillsfuture)",
      "before_index": 7,
      "after_index": 12,
      "issues": [],
      "measures": [
        {
          "key": "industry.matched_count",
          "label": "SSIC industry nodes covered",
          "better": "higher",
          "unit": "number",
          "before": 22.0,
          "after": 22.0,
          "change": 0.0,
          "relative_change": 0.0,
          "verdict": "unchanged"
        },
        {
          "key": "industry.coverage",
          "label": "Industry coverage",
          "better": "higher",
          "unit": "rate",
          "before": 0.268,
          "after": 0.268,
          "change": 0.0,
          "relative_change": 0.0,
          "verdict": "unchanged"
        },
        {
          "key": "occupational.matched_count",
          "label": "SSOC occupational nodes covered",
          "better": "higher",
          "unit": "number",
          "before": 42.0,
          "after": 43.0,
          "change": 1.0,
          "relative_change": 0.023809523809523808,
          "verdict": "improved"
        },
        {
          "key": "occupational.coverage",
          "label": "Occupational coverage",
          "better": "higher",
          "unit": "rate",
          "before": 0.29,
          "after": 0.297,
          "change": 0.007000000000000006,
          "relative_change": 0.024137931034482783,
          "verdict": "improved"
        }
      ]
    },
    {
      "metric_id": "saturation",
      "label": "saturation (baseline=stage1_skillsfuture, incoming=skillsfuture, judge={cache:judge_cache.json,candidates:5,levels:tags,model:gpt-6-luna,new_at_or_above:0.8,same_at_or_above:0.8,workers:8})",
      "before_index": 8,
      "after_index": 13,
      "issues": [],
      "measures": [
        {
          "key": "add_candidates",
          "label": "Add candidates",
          "better": "lower",
          "unit": "number",
          "before": 398.0,
          "after": 393.0,
          "change": -5.0,
          "relative_change": -0.01256281407035176,
          "verdict": "improved"
        },
        {
          "key": "review_ambiguous",
          "label": "Concepts needing review",
          "better": "lower",
          "unit": "number",
          "before": 1734.0,
          "after": 1737.0,
          "change": 3.0,
          "relative_change": 0.0017301038062283738,
          "verdict": "deteriorated"
        },
        {
          "key": "new_rate_min",
          "label": "New-items rate (min)",
          "better": "lower",
          "unit": "rate",
          "before": 0.092,
          "after": 0.091,
          "change": -0.0010000000000000009,
          "relative_change": -0.010869565217391314,
          "verdict": "improved"
        },
        {
          "key": "new_rate_max",
          "label": "New-items rate (max)",
          "better": "lower",
          "unit": "rate",
          "before": 0.495,
          "after": 0.494,
          "change": -0.0010000000000000009,
          "relative_change": -0.002020202020202022,
          "verdict": "improved"
        },
        {
          "key": "reviewed",
          "label": "Incoming concepts reviewed",
          "better": null,
          "unit": "number",
          "before": 4308.0,
          "after": 4308.0,
          "change": 0.0,
          "relative_change": 0.0,
          "verdict": "unchanged"
        },
        {
          "key": "new_placements",
          "label": "Known concepts in a new placement",
          "better": null,
          "unit": "number",
          "before": 47.0,
          "after": 49.0,
          "change": 2.0,
          "relative_change": 0.0425531914893617,
          "verdict": "changed"
        },
        {
          "key": "review_other_level",
          "label": "Known concepts at another level",
          "better": null,
          "unit": "number",
          "before": 46.0,
          "after": 46.0,
          "change": 0.0,
          "relative_change": 0.0,
          "verdict": "unchanged"
        }
      ]
    },
    {
      "metric_id": "saturation",
      "label": "saturation (baseline=stage1_skillsfuture, incoming=onet, judge={cache:judge_cache_onet.json,candidates:5,levels:tags,model:gpt-6-luna,new_at_or_above:0.8,same_at_or_above:0.8,workers:8})",
      "before_index": 9,
      "after_index": 14,
      "issues": [],
      "measures": [
        {
          "key": "add_candidates",
          "label": "Add candidates",
          "better": "lower",
          "unit": "number",
          "before": 312.0,
          "after": 179.0,
          "change": -133.0,
          "relative_change": -0.42628205128205127,
          "verdict": "improved"
        },
        {
          "key": "review_ambiguous",
          "label": "Concepts needing review",
          "better": "lower",
          "unit": "number",
          "before": 736.0,
          "after": 724.0,
          "change": -12.0,
          "relative_change": -0.016304347826086956,
          "verdict": "improved"
        },
        {
          "key": "new_rate_min",
          "label": "New-items rate (min)",
          "better": "lower",
          "unit": "rate",
          "before": 0.283,
          "after": 0.162,
          "change": -0.12099999999999997,
          "relative_change": -0.4275618374558303,
          "verdict": "improved"
        },
        {
          "key": "new_rate_max",
          "label": "New-items rate (max)",
          "better": "lower",
          "unit": "rate",
          "before": 0.951,
          "after": 0.819,
          "change": -0.132,
          "relative_change": -0.13880126182965302,
          "verdict": "improved"
        },
        {
          "key": "reviewed",
          "label": "Incoming concepts reviewed",
          "better": null,
          "unit": "number",
          "before": 1102.0,
          "after": 1102.0,
          "change": 0.0,
          "relative_change": 0.0,
          "verdict": "unchanged"
        },
        {
          "key": "new_placements",
          "label": "Known concepts in a new placement",
          "better": null,
          "unit": "number",
          "before": 11.0,
          "after": 11.0,
          "change": 0.0,
          "relative_change": 0.0,
          "verdict": "unchanged"
        },
        {
          "key": "review_other_level",
          "label": "Known concepts at another level",
          "better": null,
          "unit": "number",
          "before": 9.0,
          "after": 9.0,
          "change": 0.0,
          "relative_change": 0.0,
          "verdict": "unchanged"
        }
      ]
    }
  ],
  "comparable": true
};

export const overallComparison = {
  "comparability": {
    "before_run_id": "20428471e5af436b93faa39d1f25e472",
    "after_run_id": "20428471e5af436b93faa39d1f25e472",
    "before_subject": "stage0_original",
    "after_subject": "stage2_onet",
    "before_definitions_version": 1,
    "after_definitions_version": 1,
    "issues": [],
    "metrics": [
      {
        "metric_id": "integrity",
        "before_index": 0,
        "after_index": 10,
        "issues": []
      },
      {
        "metric_id": "depth",
        "before_index": 1,
        "after_index": 11,
        "issues": []
      },
      {
        "metric_id": "breadth",
        "before_index": 2,
        "after_index": 12,
        "issues": []
      },
      {
        "metric_id": "saturation",
        "before_index": 3,
        "after_index": 13,
        "issues": []
      },
      {
        "metric_id": "saturation",
        "before_index": 4,
        "after_index": 14,
        "issues": []
      }
    ],
    "comparable": true
  },
  "metrics": [
    {
      "metric_id": "integrity",
      "label": "integrity (dataset=stage0_original)",
      "before_index": 0,
      "after_index": 10,
      "issues": [],
      "measures": [
        {
          "key": "exact_duplicate_rows.categories",
          "label": "Categories in exact-duplicate clusters",
          "better": "lower",
          "unit": "number",
          "before": 2.0,
          "after": 56.0,
          "change": 54.0,
          "relative_change": 27.0,
          "verdict": "deteriorated"
        },
        {
          "key": "exact_duplicate_rows.specialities",
          "label": "Specialities in exact-duplicate clusters",
          "better": "lower",
          "unit": "number",
          "before": 51.0,
          "after": 188.0,
          "change": 137.0,
          "relative_change": 2.6862745098039214,
          "verdict": "deteriorated"
        },
        {
          "key": "exact_duplicate_rows.tags",
          "label": "Tags in exact-duplicate clusters",
          "better": "lower",
          "unit": "number",
          "before": 0.0,
          "after": 0.0,
          "change": 0.0,
          "relative_change": null,
          "verdict": "unchanged"
        },
        {
          "key": "fuzzy_duplicate_pairs.categories",
          "label": "Fuzzy-duplicate category pairs",
          "better": "lower",
          "unit": "number",
          "before": 1.0,
          "after": 2.0,
          "change": 1.0,
          "relative_change": 1.0,
          "verdict": "deteriorated"
        },
        {
          "key": "fuzzy_duplicate_pairs.specialities",
          "label": "Fuzzy-duplicate speciality pairs",
          "better": "lower",
          "unit": "number",
          "before": 28.0,
          "after": 98.0,
          "change": 70.0,
          "relative_change": 2.5,
          "verdict": "deteriorated"
        },
        {
          "key": "fuzzy_duplicate_pairs.tags",
          "label": "Fuzzy-duplicate tag pairs",
          "better": "lower",
          "unit": "number",
          "before": 57.0,
          "after": 132.0,
          "change": 75.0,
          "relative_change": 1.3157894736842106,
          "verdict": "deteriorated"
        },
        {
          "key": "orphans.categories_without_service",
          "label": "Categories without a service",
          "better": "lower",
          "unit": "number",
          "before": 0.0,
          "after": 0.0,
          "change": 0.0,
          "relative_change": null,
          "verdict": "unchanged"
        },
        {
          "key": "orphans.specialities_without_category",
          "label": "Specialities without a category",
          "better": "lower",
          "unit": "number",
          "before": 0.0,
          "after": 0.0,
          "change": 0.0,
          "relative_change": null,
          "verdict": "unchanged"
        },
        {
          "key": "orphans.tags_unused_by_any_speciality",
          "label": "Tags no speciality uses",
          "better": "lower",
          "unit": "number",
          "before": 1.0,
          "after": 1.0,
          "change": 0.0,
          "relative_change": 0.0,
          "verdict": "unchanged"
        },
        {
          "key": "orphans.dangling_speciality_tag_links",
          "label": "Dangling speciality-tag links",
          "better": "lower",
          "unit": "number",
          "before": 0.0,
          "after": 0.0,
          "change": 0.0,
          "relative_change": null,
          "verdict": "unchanged"
        },
        {
          "key": "dead_ends.categories_with_zero_specialities",
          "label": "Dead-end categories",
          "better": "lower",
          "unit": "number",
          "before": 14.0,
          "after": 12.0,
          "change": -2.0,
          "relative_change": -0.14285714285714285,
          "verdict": "improved"
        },
        {
          "key": "dead_ends.specialities_with_zero_tags",
          "label": "Dead-end specialities",
          "better": "lower",
          "unit": "number",
          "before": 1.0,
          "after": 1.0,
          "change": 0.0,
          "relative_change": 0.0,
          "verdict": "unchanged"
        },
        {
          "key": "dead_ends.category_dead_end_rate",
          "label": "Dead-end category rate",
          "better": "lower",
          "unit": "rate",
          "before": 0.12727272727272726,
          "after": 0.08053691275167785,
          "change": -0.04673581452104941,
          "relative_change": -0.36720997123681687,
          "verdict": "improved"
        },
        {
          "key": "dead_ends.speciality_dead_end_rate",
          "label": "Dead-end speciality rate",
          "better": "lower",
          "unit": "rate",
          "before": 0.0016863406408094434,
          "after": 0.0005906674542232723,
          "change": -0.0010956731865861711,
          "relative_change": -0.6497341996455995,
          "verdict": "improved"
        }
      ]
    },
    {
      "metric_id": "depth",
      "label": "depth (dataset=stage0_original)",
      "before_index": 1,
      "after_index": 11,
      "issues": [],
      "measures": [
        {
          "key": "speciality_to_tag.mean",
          "label": "Tags per speciality (mean)",
          "better": "higher",
          "unit": "number",
          "before": 2.43,
          "after": 1.7,
          "change": -0.7300000000000002,
          "relative_change": -0.30041152263374493,
          "verdict": "deteriorated"
        },
        {
          "key": "speciality_to_tag.median",
          "label": "Tags per speciality (median)",
          "better": "higher",
          "unit": "number",
          "before": 2.0,
          "after": 1.0,
          "change": -1.0,
          "relative_change": -0.5,
          "verdict": "deteriorated"
        },
        {
          "key": "category_to_speciality.mean",
          "label": "Specialities per category (mean)",
          "better": null,
          "unit": "number",
          "before": 5.39,
          "after": 11.36,
          "change": 5.97,
          "relative_change": 1.1076066790352506,
          "verdict": "changed"
        },
        {
          "key": "category_to_speciality.median",
          "label": "Specialities per category (median)",
          "better": null,
          "unit": "number",
          "before": 5.0,
          "after": 6.0,
          "change": 1.0,
          "relative_change": 0.2,
          "verdict": "changed"
        },
        {
          "key": "category_to_speciality.n",
          "label": "Categories",
          "better": null,
          "unit": "number",
          "before": 110.0,
          "after": 149.0,
          "change": 39.0,
          "relative_change": 0.35454545454545455,
          "verdict": "changed"
        },
        {
          "key": "speciality_to_tag.n",
          "label": "Specialities",
          "better": null,
          "unit": "number",
          "before": 593.0,
          "after": 1693.0,
          "change": 1100.0,
          "relative_change": 1.854974704890388,
          "verdict": "changed"
        }
      ]
    },
    {
      "metric_id": "breadth",
      "label": "breadth (target=stage0_original)",
      "before_index": 2,
      "after_index": 12,
      "issues": [],
      "measures": [
        {
          "key": "industry.matched_count",
          "label": "SSIC industry nodes covered",
          "better": "higher",
          "unit": "number",
          "before": 18.0,
          "after": 22.0,
          "change": 4.0,
          "relative_change": 0.2222222222222222,
          "verdict": "improved"
        },
        {
          "key": "industry.coverage",
          "label": "Industry coverage",
          "better": "higher",
          "unit": "rate",
          "before": 0.22,
          "after": 0.268,
          "change": 0.048000000000000015,
          "relative_change": 0.21818181818181825,
          "verdict": "improved"
        },
        {
          "key": "occupational.matched_count",
          "label": "SSOC occupational nodes covered",
          "better": "higher",
          "unit": "number",
          "before": 16.0,
          "after": 43.0,
          "change": 27.0,
          "relative_change": 1.6875,
          "verdict": "improved"
        },
        {
          "key": "occupational.coverage",
          "label": "Occupational coverage",
          "better": "higher",
          "unit": "rate",
          "before": 0.11,
          "after": 0.297,
          "change": 0.187,
          "relative_change": 1.7,
          "verdict": "improved"
        }
      ]
    },
    {
      "metric_id": "saturation",
      "label": "saturation (baseline=stage0_original, incoming=skillsfuture, judge={cache:judge_cache.json,candidates:5,levels:tags,model:gpt-6-luna,new_at_or_above:0.8,same_at_or_above:0.8,workers:8})",
      "before_index": 3,
      "after_index": 13,
      "issues": [],
      "measures": [
        {
          "key": "add_candidates",
          "label": "Add candidates",
          "better": "lower",
          "unit": "number",
          "before": 1895.0,
          "after": 393.0,
          "change": -1502.0,
          "relative_change": -0.7926121372031663,
          "verdict": "improved"
        },
        {
          "key": "review_ambiguous",
          "label": "Concepts needing review",
          "better": "lower",
          "unit": "number",
          "before": 1701.0,
          "after": 1737.0,
          "change": 36.0,
          "relative_change": 0.021164021164021163,
          "verdict": "deteriorated"
        },
        {
          "key": "new_rate_min",
          "label": "New-items rate (min)",
          "better": "lower",
          "unit": "rate",
          "before": 0.44,
          "after": 0.091,
          "change": -0.349,
          "relative_change": -0.7931818181818181,
          "verdict": "improved"
        },
        {
          "key": "new_rate_max",
          "label": "New-items rate (max)",
          "better": "lower",
          "unit": "rate",
          "before": 0.835,
          "after": 0.494,
          "change": -0.34099999999999997,
          "relative_change": -0.4083832335329341,
          "verdict": "improved"
        },
        {
          "key": "reviewed",
          "label": "Incoming concepts reviewed",
          "better": null,
          "unit": "number",
          "before": 4308.0,
          "after": 4308.0,
          "change": 0.0,
          "relative_change": 0.0,
          "verdict": "unchanged"
        },
        {
          "key": "new_placements",
          "label": "Known concepts in a new placement",
          "better": null,
          "unit": "number",
          "before": 30.0,
          "after": 49.0,
          "change": 19.0,
          "relative_change": 0.6333333333333333,
          "verdict": "changed"
        },
        {
          "key": "review_other_level",
          "label": "Known concepts at another level",
          "better": null,
          "unit": "number",
          "before": 32.0,
          "after": 46.0,
          "change": 14.0,
          "relative_change": 0.4375,
          "verdict": "changed"
        }
      ]
    },
    {
      "metric_id": "saturation",
      "label": "saturation (baseline=stage0_original, incoming=onet, judge={cache:judge_cache_onet.json,candidates:5,levels:tags,model:gpt-6-luna,new_at_or_above:0.8,same_at_or_above:0.8,workers:8})",
      "before_index": 4,
      "after_index": 14,
      "issues": [],
      "measures": [
        {
          "key": "add_candidates",
          "label": "Add candidates",
          "better": "lower",
          "unit": "number",
          "before": 483.0,
          "after": 179.0,
          "change": -304.0,
          "relative_change": -0.629399585921325,
          "verdict": "improved"
        },
        {
          "key": "review_ambiguous",
          "label": "Concepts needing review",
          "better": "lower",
          "unit": "number",
          "before": 572.0,
          "after": 724.0,
          "change": 152.0,
          "relative_change": 0.26573426573426573,
          "verdict": "deteriorated"
        },
        {
          "key": "new_rate_min",
          "label": "New-items rate (min)",
          "better": "lower",
          "unit": "rate",
          "before": 0.438,
          "after": 0.162,
          "change": -0.276,
          "relative_change": -0.6301369863013699,
          "verdict": "improved"
        },
        {
          "key": "new_rate_max",
          "label": "New-items rate (max)",
          "better": "lower",
          "unit": "rate",
          "before": 0.957,
          "after": 0.819,
          "change": -0.138,
          "relative_change": -0.14420062695924768,
          "verdict": "improved"
        },
        {
          "key": "reviewed",
          "label": "Incoming concepts reviewed",
          "better": null,
          "unit": "number",
          "before": 1102.0,
          "after": 1102.0,
          "change": 0.0,
          "relative_change": 0.0,
          "verdict": "unchanged"
        },
        {
          "key": "new_placements",
          "label": "Known concepts in a new placement",
          "better": null,
          "unit": "number",
          "before": 8.0,
          "after": 11.0,
          "change": 3.0,
          "relative_change": 0.375,
          "verdict": "changed"
        },
        {
          "key": "review_other_level",
          "label": "Known concepts at another level",
          "better": null,
          "unit": "number",
          "before": 8.0,
          "after": 9.0,
          "change": 1.0,
          "relative_change": 0.125,
          "verdict": "changed"
        }
      ]
    }
  ],
  "comparable": true
};
