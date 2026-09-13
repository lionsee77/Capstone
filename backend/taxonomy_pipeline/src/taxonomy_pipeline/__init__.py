"""Taxonomy validation pipeline: pluggable taxonomy datasets, pluggable
quality metrics, run via a config-driven CLI or a FastAPI service.

See README.md for the plugin points (datasets/, metrics/) and how the CLI
and the API both call the same `pipeline.run_pipeline`.
"""

__version__ = "0.1.0"
