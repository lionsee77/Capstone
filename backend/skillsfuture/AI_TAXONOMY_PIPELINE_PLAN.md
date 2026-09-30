# AI-Assisted SkillsFuture Taxonomy Pipeline

## 1. Purpose

This pipeline uses the SkillsFuture datasets and an AI model to classify skills
into the project's existing taxonomy:

```text
services → categories → specialities → tags
```

The pipeline should reuse existing categories and specialities whenever they are
appropriate. If no suitable classification exists, it may propose a new
category or speciality for human review.

The AI is an assistant, not the final authority. No AI result should directly
modify the database.

## 2. Source data

The source workbooks are stored in this folder:

- `jobsandskills-skillsfuture-unique-skills-list.xlsx`
  - Canonical list of Unique Skills.
  - Used primarily to create the `tags` records.
  - Includes skill IDs, titles, descriptions, skill type, Emerging Skills, and
    CASL indicators.

- `jobsandskills-skillsfuture-skills-framework-dataset.xlsx`
  - Contains sectors, job roles, tasks, TSCs, CCSs, proficiency levels, and
    descriptions.
  - Used to create categories, specialities, and initial skill relationships.

- `jobsandskills-skillsfuture-tsc-to-unique-skills-mapping.xlsx`
  - Maps Skills Framework/TSC skills to updated Unique Skills.
  - Used to connect specialities to canonical tags.

## 3. Current deterministic pipeline

`generate_skillsfuture_taxonomy.py` creates a parallel taxonomy without
changing the existing database:

```text
existing services
└── SkillsFuture sectors              → sf_categories
    └── job roles                     → sf_specialities
        └── Unique Skills             → sf_tags
            └── mapping relationships → sf_speciality_tags
```

All SkillsFuture sectors currently belong to the existing `Industry` service.
The generated records use separate `sf_` tables so the original taxonomy is
preserved for comparison and rollback.

The output is:

```text
skillsfuture_taxonomy.sql
```

## 4. AI classification pipeline

### Stage A: Prepare the classification input

1. Generate the deterministic SkillsFuture taxonomy.
2. Validate the generated SQL.
3. Identify tags that are not present in `sf_speciality_tags`.
4. Read descriptions and metadata for those tags from the Unique Skills file.
5. Load all current categories and specialities as candidate classifications.

### Stage B: Ask the AI for proposals

For each batch of unmapped skills, provide the AI with:

- Skill ID
- Skill title
- Skill description
- Skill type
- Existing categories
- Existing specialities and their categories
- Relevant sector information, where available

The AI must return one proposal per skill with:

```json
{
  "tag_id": "...",
  "action": "assign_existing",
  "category": "...",
  "speciality": "...",
  "confidence": 0.0,
  "reason": "..."
}
```

### Stage C: Allowed classification outcomes

#### `assign_existing`

Use an existing category and speciality because the skill fits an existing
branch.

#### `propose_new_speciality`

Use an existing category but propose a new speciality because no existing
speciality is suitable.

#### `propose_new_category`

Propose a new category and speciality because the existing taxonomy does not
represent the skill's domain.

The AI must not invent a new service. New services require a separate project
decision because services are the highest level of the taxonomy.

## 5. Review workflow

The AI classifier writes proposals to:

```text
classification_proposals.json
classification_proposals.csv
```

Each proposal starts with:

```text
review_status = pending
```

A project member reviews each proposal and changes the status to one of:

- `approved`
- `rejected`
- `needs_review`

The reviewer should check:

- Whether the category is appropriate
- Whether the speciality already exists under another name
- Whether the proposal duplicates another proposal
- Whether the skill description supports the classification
- Whether a new category or speciality is genuinely necessary

Only approved proposals should be converted into SQL.

## 6. Confidence policy

Confidence is a recommendation, not a guarantee.

Suggested review thresholds:

| Confidence | Suggested handling |
|---:|---|
| `0.85–1.00` | Can receive faster review; still validate duplicates |
| `0.60–0.84` | Manual review required |
| Below `0.60` | Do not approve automatically |

Any proposal for a new category should receive manual review regardless of its
confidence score.

## 7. Safety and quality controls

The pipeline must:

- Never write directly to MySQL from the AI classifier
- Never delete existing taxonomy records automatically
- Preserve original SkillsFuture skill IDs
- Prevent duplicate category names
- Prevent duplicate speciality names within a category
- Prevent duplicate speciality/tag relationships
- Verify all parent references before SQL generation
- Keep source-generated and AI-generated classifications distinguishable
- Store the AI explanation alongside the proposal
- Make the process repeatable from the original Excel files

The validator should be run after every SQL generation:

```bash
python skillsfuture/validate_skillsfuture_taxonomy.py
```

## 8. Planned scripts

```text
skillsfuture/
├── generate_skillsfuture_taxonomy.py
├── classify_unmapped_skills.py
├── validate_skillsfuture_taxonomy.py
├── approve_classifications.py          # planned
├── skillsfuture_taxonomy.sql
└── classification_proposals.csv
```

### Existing scripts

- `generate_skillsfuture_taxonomy.py`
  - Reads the Excel files.
  - Generates the deterministic `sf_` taxonomy SQL.
  - Does not connect to MySQL.

- `classify_unmapped_skills.py`
  - Sends unmapped tags to the configured OpenAI model.
  - Produces JSON and CSV proposals.
  - Does not modify MySQL or the generated SQL.

- `validate_skillsfuture_taxonomy.py`
  - Checks duplicates, malformed rows, broken references, dead-end categories,
    specialities without tags, and unused tags.

### Planned script

- `approve_classifications.py`
  - Reads the reviewed CSV.
  - Uses only rows marked `approved`.
  - Creates SQL inserts for approved categories, specialities, tags, and links.
  - Runs validation before reporting success.

## 9. Suggested implementation order

1. Generate and validate the deterministic taxonomy.
2. Run the AI classifier on unmapped tags.
3. Review and approve proposals in the CSV.
4. Implement the approval-to-SQL script.
5. Generate an approved SQL file.
6. Validate the approved SQL.
7. Import it into a separate database or separate `sf_` tables first.
8. Compare it with the original taxonomy.
9. Only replace or merge the production taxonomy after team approval.

## 10. Environment configuration

Copy the environment template before running the AI classifier:

```bash
cp .env.example .env
```

The relevant settings are:

```env
OPENAI_API_KEY=your-openai-api-key
OPENAI_MODEL=gpt-4o-mini
```

The classifier loads `.env` automatically. Do not commit `.env` or expose the
API key.

## 11. Definition of done

The pipeline is ready for use when:

- Every generated row has a valid parent relationship
- Duplicate names and links are resolved or explicitly accepted
- AI proposals have been reviewed
- New categories and specialities are approved by the project team
- The approved SQL passes validation
- The original source data and generated outputs remain reproducible
- The final taxonomy can be imported without modifying the original taxonomy
  unexpectedly
