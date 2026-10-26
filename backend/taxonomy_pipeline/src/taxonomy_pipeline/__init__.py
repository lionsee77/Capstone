"""Taxonomy validation pipeline: pluggable taxonomy datasets, pluggable
quality metrics, run via a config-driven CLI or a FastAPI service.

The package is laid out by bounded context (#104), each importing only the
public names of the contexts below it:

- `taxonomy/`: the taxonomy model and the dataset source registry, with the
  SSIC/SSOC classification references in `taxonomy/references/`;
- `matching/`: anything that answers "is this the same concept?";
- `quality/`: the scoring metrics and their frozen definitions;
- `providers/`: what is specific to one external source (SkillsFuture), as
  `SourceProvider` adapters;
- `enrichment/`: placing new tags (Case A, Case B), merging, review and integration;
- `app/`: the use cases, one per CLI command, that `cli.py` and `api/` call.

`llm.py` builds the one OpenAI client the judge and Case B share. See README.md
for the plugin points (`taxonomy/`, `quality/`, `providers/`) and how the CLI and
the API both call the same `app.scoring.run_pipeline`.
"""

__version__ = "0.1.0"
