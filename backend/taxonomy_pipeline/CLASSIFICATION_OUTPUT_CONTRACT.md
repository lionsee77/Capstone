# Classification output contract

**Producers:** Yi Jie (Case A), Zay Nin (Case B). **Consumer:** Bryan.

Return a JSON array with one object per proposed placement. Both cases use exactly
the same eight fields. Producers only need to generate JSON; Pydantic is used by
the consumer to validate it.

## Fields

All eight fields are required. Extra fields are rejected.

| Field | JSON type | Meaning |
|---|---|---|
| `source_dataset` | string | Source dataset name |
| `tag_id` | string | Original tag ID, e.g. `"42"` |
| `original_tag` | string | Original tag text, unchanged |
| `original_classification` | string | `"classified"` for Case A; `"unclassified"` for Case B |
| `category` | string or null | Proposed Ankit category name |
| `speciality` | string or null | Proposed Ankit speciality name within that category |
| `confidence` | number or null | Confidence in this placement, from `0` to `1` inclusive |
| `reason` | string | Short placement explanation, or why no placement was found |

`original_classification` describes the tag **in its source dataset**, before Case
A/B processing. Category and speciality describe the **proposed Ankit placement**.

## Examples

Names below are illustrative; use the agreed Ankit taxonomy when producing outputs.

```json
[
  {
    "source_dataset": "categorized-skills",
    "tag_id": "42",
    "original_tag": "Data visualization",
    "original_classification": "classified",
    "category": "Data analytics",
    "speciality": "Data presentation",
    "confidence": 0.95,
    "reason": "The supplied category maps to data presentation."
  },
  {
    "source_dataset": "plain-skills",
    "tag_id": "42",
    "original_tag": "Data visualization",
    "original_classification": "unclassified",
    "category": "Data analytics",
    "speciality": "Business communication",
    "confidence": 0.88,
    "reason": "Visualizing data helps communicate findings."
  },
  {
    "source_dataset": "plain-skills",
    "tag_id": "48",
    "original_tag": "General practice",
    "original_classification": "unclassified",
    "category": null,
    "speciality": null,
    "confidence": null,
    "reason": "Insufficient context to select a speciality."
  }
]
```

## Rules

- Identify tags by `(source_dataset, tag_id)`; IDs can repeat across datasets.
- For multiple placements, return separate objects with the same source identity,
  original tag, and original classification.
- Category, speciality, and confidence must either all have values or all be `null`.
- Strings must contain non-whitespace text. Use numeric confidence (`0.88`), not a
  quoted number (`"0.88"`), percentage, or boolean.
- Confidence helps review; it does not approve a placement or confirm duplicate tags.
- The taxonomy is supplied separately. No comparison results, version fields, or
  internal review/preview records belong in this payload.

## Implementation reference

The JSON format and field validation are implemented. Taxonomy lookup,
deduplication, and merge processing are still pending.

- [JSON Schema](schemas/classification_output.schema.json)
- [Python validator](src/taxonomy_pipeline/classification/models.py): `CLASSIFICATION_OUTPUTS.validate_json(payload)`
- [Case A fixture](tests/fixtures/classification/case_a.json)
- [Case B fixture](tests/fixtures/classification/case_b.json)
- [All fixture scenarios](tests/fixtures/classification/README.md)
