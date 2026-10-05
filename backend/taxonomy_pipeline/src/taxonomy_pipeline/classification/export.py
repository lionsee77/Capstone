"""Write the merge preview as a spreadsheet-readable CSV with stable columns.

The file contains review evidence, counts, and checks; it is not a database import.
Text that spreadsheet applications could execute as a formula is escaped on export.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .models import MergePreview, PreviewRow


# Preserve ordinary text while preventing spreadsheet formula execution in source-controlled cells.
def _csv_value(value: Any) -> Any:
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


# Export proposed and omitted placements together with counts and validation findings to one CSV.
def export_preview_csv(preview: MergePreview, path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(PreviewRow.model_fields))
        writer.writeheader()
        for row in preview.rows:
            writer.writerow({name: _csv_value(value) for name, value in row.model_dump(mode="json").items()})
    return destination
