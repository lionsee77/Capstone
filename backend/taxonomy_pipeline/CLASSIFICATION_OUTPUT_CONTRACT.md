# Classification output contract

**Producers:** Yi Jie (Case A), Zay Nin (Case B). **Consumer:** Bryan.

Return a JSON array with one object per proposed placement. Both cases use exactly
the same nine fields. Producers only need to generate JSON; Pydantic is used by
the consumer to validate it.

## Ankit's four taxonomy tiers

```text
Service -> Category -> Speciality -> Tag
```

| Tier | SQL table | Relationship | Output field |
|---|---|---|---|
| Service | [services](../sql-files-nus/services.sql) | Top level: Industry, Function, Subject, or Others | `service` |
| Category | [categories](../sql-files-nus/categories.sql) | `service_id` refers to `services.id` | `category` |
| Speciality | [specialities](../sql-files-nus/specialities.sql) | `category_id` refers to `categories.id` | `speciality` |
| Tag | [tags](../sql-files-nus/tags.sql) | Linked to specialities through `speciality_tags` | `tag` |

Example path from the SQL:

```text
Industry -> Banking -> Regulatory Reporting (Basel/IFRS9) -> ifrs9
```

[speciality_tags](../sql-files-nus/speciality_tags.sql) joins `speciality_id` and
`tag_id`; it is not a fifth tier. A tag can belong to multiple specialities.

Producers supply all four tiers explicitly. The category must belong to the supplied
service, and the speciality must belong to that category. Ambiguous paths need review.
The payload's `tag_id` is the source dataset's ID, not Ankit's `tags.id`.

## Fields

All nine fields are required. The revised contract permits no extra fields.

| Field | JSON type | Meaning |
|---|---|---|
| `source_dataset` | string | Source dataset name |
| `tag_id` | string | Original tag ID, e.g. `"42"` |
| `tag` | string | Unchanged tag text from the source dataset; this is the tag to insert into the database |
| `original_classification` | string | `"classified"` for Case A; `"unclassified"` for Case B |
| `service` | string or null | Proposed Ankit service: `"Industry"`, `"Function"`, `"Subject"`, or `"Others"` |
| `category` | string or null | Proposed Ankit category name |
| `speciality` | string or null | Proposed Ankit speciality name within that category |
| `confidence` | number or null | Confidence in this placement, from `0` to `1` inclusive |
| `reason` | string | Short placement explanation, or why no placement was found |

`original_classification` describes the tag **in its source dataset**, before Case
A/B processing. `tag` preserves the source text and supplies the database tag name.
Service, category, and speciality describe its **proposed Ankit placement**.

## Examples

Names below are illustrative; use the agreed Ankit taxonomy when producing outputs.

```json
[
  {
    "source_dataset": "categorized-skills",
    "tag_id": "42",
    "tag": "Data visualization",
    "original_classification": "classified",
    "service": "Subject",
    "category": "Data analytics",
    "speciality": "Data presentation",
    "confidence": 0.95,
    "reason": "The supplied category maps to data presentation."
  },
  {
    "source_dataset": "plain-skills",
    "tag_id": "42",
    "tag": "Data visualization",
    "original_classification": "unclassified",
    "service": "Subject",
    "category": "Data analytics",
    "speciality": "Business communication",
    "confidence": 0.88,
    "reason": "Visualizing data helps communicate findings."
  },
  {
    "source_dataset": "plain-skills",
    "tag_id": "48",
    "tag": "General practice",
    "original_classification": "unclassified",
    "service": null,
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
  tag text, and original classification.
- Service, category, speciality, and confidence must either all have values or all be `null`.
- Keep `tag` unchanged and nonblank, including when its placement is unresolved.
- Strings must contain non-whitespace text. Use numeric confidence (`0.88`), not a
  quoted number (`"0.88"`), percentage, or boolean.
- Confidence helps review; it does not approve a placement or confirm duplicate tags.
- The taxonomy is supplied separately. No comparison results, version fields, or
  internal review/preview records belong in this payload.

## Implementation reference

This document defines the revised nine-field contract. The model, JSON Schema,
and fixtures below still use the previous eight-field format and need updating
to accept `tag` and the explicit `service` field. The examples above are not yet
accepted by the current validator.

- [JSON Schema](schemas/classification_output.schema.json)
- [Python validator](src/taxonomy_pipeline/classification/models.py): `CLASSIFICATION_OUTPUTS.validate_json(payload)`
- [Case A fixture](tests/fixtures/classification/case_a.json)
- [Case B fixture](tests/fixtures/classification/case_b.json)
- [All fixture scenarios](tests/fixtures/classification/README.md)
