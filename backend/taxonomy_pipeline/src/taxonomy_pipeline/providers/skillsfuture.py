"""SkillsFuture: the constants Case B and the CLI need to read SkillsFuture data.

- `TAG_ID_OFFSET`: the generated SkillsFuture taxonomy numbers its tags from 3000,
  in sorted skill-ID order, so tag `3000 + n` is the n-th unique skill.
- `UNIQUE_SKILLS_WORKBOOK`: the unique-skills list Case B reads each skill's title,
  description and type from, for prompt context.

A second source (#101) gets its own module beside this one rather than more
constants here.
"""
from __future__ import annotations

from pathlib import Path

TAG_ID_OFFSET = 3000
# backend/skillsfuture/ when installed in editable mode (src/taxonomy_pipeline/providers/ -> backend/).
UNIQUE_SKILLS_WORKBOOK = (
    Path(__file__).resolve().parents[4] / "skillsfuture" / "jobsandskills-skillsfuture-unique-skills-list.xlsx"
)
