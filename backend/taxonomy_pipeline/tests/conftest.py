from __future__ import annotations

import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _open_api(monkeypatch, tmp_path_factory):
    """Let API tests call the app without a token and post datasets from pytest's temp dirs.

    `test_api_security.py` restores the real settings to test the checks themselves.
    """
    monkeypatch.setenv("TAXONOMY_API_AUTH", "off")
    monkeypatch.setenv("TAXONOMY_DATA_ROOTS", os.pathsep.join([
        str(FIXTURES.parents[2]), str(tmp_path_factory.getbasetemp()),
    ]))


@pytest.fixture
# Save a real reviewed merge run with same-name branches, scored matches and an audit event.
def saved_proposal_run(tmp_path: Path) -> Path:
    import json
    from unittest.mock import Mock

    import numpy as np

    from taxonomy_pipeline.classification.models import ResolutionReport, ReviewDecision
    from taxonomy_pipeline.classification.runner import run_merge_preview
    from taxonomy_pipeline.datasets.base import TaxonomyDataset

    baseline = TaxonomyDataset(
        name="baseline", services=[{"id": 1, "name": "Subject"}, {"id": 2, "name": "Industry"}],
        categories=[{"id": 10, "name": "Computing", "service_id": 1},
                    {"id": 11, "name": "Computing", "service_id": 2}],
        specialities=[{"id": 20, "name": "Data visualization", "category_id": 10},
                      {"id": 21, "name": "Data visualization", "category_id": 11}],
        tags=[], speciality_tags=[],
    )
    (tmp_path / "baseline.json").write_text(baseline.model_dump_json(), encoding="utf-8")
    producer = tmp_path / "producer.json"
    producer.write_text(json.dumps([{
        "source_dataset": "feed", "tag_id": "1", "tag": "Fresh skill", "original_classification": "classified",
        "service": "Subject", "category": "Computing", "speciality": "Data visualizatio",
        "confidence": 0.8, "reason": "Producer suggestion",
    }]), encoding="utf-8")
    directory = tmp_path / "merge_runs" / "reviewed"
    matcher = Mock(similarity=lambda names, existing: np.full((len(names), len(existing)), 0.8))
    run_merge_preview([producer], baseline, out_dir=directory, semantic_matcher=matcher)
    report = ResolutionReport.model_validate_json((directory / "resolution.json").read_text(encoding="utf-8"))
    proposal = next(item for item in report.screening.proposals if item.level == "speciality")
    run_merge_preview([producer], baseline, out_dir=directory, semantic_matcher=matcher, decisions=[
        ReviewDecision(proposal_id=proposal.proposal_id, action="reuse", existing_concept_id=20,
                       reason="Reviewed both branches; reuse the Subject speciality"),
    ])
    return directory


@pytest.fixture
def classification_paths(tmp_path):
    from openpyxl import Workbook

    definitions = {
        "ssic": ("2025", [("A", "Agriculture"), ("01", "Accounting")]),
        "ssoc": ("2024", [("2", "Professionals"), ("24", "Business professionals"),
                           ("241", "Accountants")]),
    }
    paths = {}
    for kind, (version, rows) in definitions.items():
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = f"{kind.upper()} {version} Structure"
        sheet.append([f"{kind.upper()} {version}", f"{kind.upper()} {version} Title"])
        for row in rows:
            sheet.append(row)
        path = tmp_path / f"{kind}.xlsx"
        workbook.save(path)
        workbook.close()
        paths[kind] = path
    return paths


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Point taxonomy_pipeline.db at a throwaway SQLite file for this test."""
    from taxonomy_pipeline import db

    db_path = tmp_path / "test-runs.sqlite3"
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", db_path)
    return db_path


@pytest.fixture
def saturation_report(tmp_path):
    """Write a minimal `taxonomy-pipeline run` report holding one saturation step.

    Call it with the incoming tag concepts (`recommended_action`,
    `parent_status`, `incoming_ids`); returns the report path.
    """
    import json

    def write(concepts, *, incoming="skillsfuture", run_id="run-1", name="report.json"):
        step = {"source": incoming, "by_concept_level": {"tags": {"ambiguous_concepts": concepts}}}
        report = {"run_id": run_id, "results": [
            {"metric_id": "integrity", "status": "ok", "summary": "", "details": {}},
            {"metric_id": "saturation", "status": "ok", "summary": "", "details": {
                "incoming": [incoming], "by_concept_level": step["by_concept_level"], "steps": [step],
            }},
        ]}
        path = tmp_path / name
        path.write_text(json.dumps(report), encoding="utf-8")
        return path

    return write
