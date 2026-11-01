from __future__ import annotations

import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
CLASSIFICATION_FIXTURES = FIXTURES / "classification"
PRODUCER_FILES = [CLASSIFICATION_FIXTURES / "case_a.json", CLASSIFICATION_FIXTURES / "case_b.json"]
BASELINE_OF_RECORD = Path(__file__).resolve().parents[1] / "baselines" / "baseline_v1.json.gz"
_baseline_cache: dict[str, object] = {}


def baseline_of_record():
    """A fresh copy of the stored baseline of record (v1): real results for every scoring metric.
    Parsed once per session (it lists every saturation concept, so it is megabytes of JSON)."""
    from taxonomy_pipeline.app.scoring import read_report

    if "report" not in _baseline_cache:
        _baseline_cache["report"] = read_report(BASELINE_OF_RECORD)
    return _baseline_cache["report"].model_copy(deep=True)


def enriched_report(**edits):
    """The baseline of record re-labelled as a run scoring "enriched", with `edits` applied to its details.

    Each edit is `<metric_id>__<dotted.path>=value`, e.g. `breadth__occupational.matched_count=24`.
    """
    report = baseline_of_record().model_copy(deep=True)
    report.run_id = "enriched-run"
    for result in report.results:
        for param in ("dataset", "target", "baseline"):
            if result.params and result.params.get(param) == "original":
                result.params[param] = "enriched"
    for dataset in report.datasets:
        if dataset.name == "original":
            dataset.name = "enriched"
    for name, value in edits.items():
        metric_id, path = name.split("__")
        details = next(result.details for result in report.results if result.metric_id == metric_id)
        *parents, leaf = path.split(".")
        for parent in parents:
            details = details[parent]
        details[leaf] = value
    return report


@pytest.fixture(autouse=True)
def _open_api(monkeypatch, tmp_path_factory):
    """Let API tests call the app without a token and post datasets from pytest's temp dirs.

    `test_api_security.py` restores the real settings to test the checks themselves.
    """
    monkeypatch.setenv("TAXONOMY_API_AUTH", "off")
    monkeypatch.setenv("TAXONOMY_DATA_ROOTS", os.pathsep.join([
        str(FIXTURES.parents[2]), str(tmp_path_factory.getbasetemp()),
    ]))


@pytest.fixture(autouse=True)
def _reset_package_logging():
    """Undo the handler and level `cli.main` puts on the package logger, so one test's CLI call
    doesn't leave later tests' log records printing to its long-gone stdout."""
    import logging

    yield
    package = logging.getLogger("taxonomy_pipeline")
    for handler in list(package.handlers):
        package.removeHandler(handler)
    package.setLevel(logging.NOTSET)


@pytest.fixture
# Save a real reviewed merge run with same-name branches, scored matches and an audit event.
def saved_proposal_run(tmp_path: Path) -> Path:
    import json
    from unittest.mock import Mock

    import numpy as np

    from taxonomy_pipeline.enrichment.models import ResolutionReport, ReviewDecision
    from taxonomy_pipeline.enrichment.runner import run_merge_preview
    from taxonomy_pipeline.taxonomy.base import TaxonomyDataset

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


# Shared helpers: plain functions several test modules build on, kept here so no test module imports another.


def breadth_datasets():
    """A small target taxonomy with SSIC and SSOC references for the breadth tests."""
    from taxonomy_pipeline.taxonomy.base import TaxonomyDataset
    from taxonomy_pipeline.taxonomy.references.ssic import SsicRecord, SsicReference
    from taxonomy_pipeline.taxonomy.references.ssoc import SsocRecord, SsocReference

    return {
        "original": TaxonomyDataset(
            name="original",
            categories=[{"id": 1, "name": "Accounting", "service_id": 1},
                        {"id": 2, "name": "Retail", "service_id": 2}],
            specialities=[{"id": 10, "name": "Accountants"}],
        ),
        "ssic": SsicReference(source_path=Path("ssic.xlsx"), records=[
            SsicRecord(code="01", title="Accounting", level="division", parent_code="A"),
            SsicRecord(code="02", title="Retail", level="division", parent_code="A"),
            SsicRecord(code="A", title="Accounting", level="section", parent_code=None),
        ]),
        "ssoc": SsocReference(source_path=Path("ssoc.xlsx"), records=[
            SsocRecord(code="241", title="Accountants", level="minor_group", parent_code="24"),
            SsocRecord(code="242", title="Nurses", level="minor_group", parent_code="24"),
        ]),
    }


def classification_config_payload(classification_paths):
    """A lexical breadth config over the mini taxonomy and the generated SSIC/SSOC workbooks."""
    return {
        "datasets": [
            {"name": "original", "paths": [str(FIXTURES / "mini_original.sql")]},
            *[{"name": kind, "type": kind, "paths": [str(path)]}
              for kind, path in classification_paths.items()],
        ],
        "metrics": [{"id": "breadth", "params": {"target": "original", "matching_method": "lexical"}}],
    }


def review_candidate_file(tmp_path: Path) -> Path:
    """A producer file like Case A's: one tag offered under two candidate categories, flagged for review,
    next to an ordinary placement that needs nothing."""
    import json

    def record(tag_id: str, tag: str, category: str, speciality: str, **extra: object) -> dict[str, object]:
        return {
            "source_dataset": "case-a", "tag_id": tag_id, "tag": tag, "original_classification": "classified",
            "service": "Subject", "category": category, "speciality": speciality, "confidence": 0.5,
            "reason": "Proposal", **extra,
        }

    path = tmp_path / "case_a_output.json"
    path.write_text(json.dumps([
        record("9", "Fire Prevention", "Data analytics", "Fire Prevention", needs_review=True),
        record("9", "Fire Prevention", "Biology", "Fire Prevention", needs_review=True),
        record("10", "Spreadsheet Modelling", "Data analytics", "Data presentation"),
    ]), encoding="utf-8")
    return path


def approve_data_analytics():
    """The decision approving tag 9's Data analytics candidate from `review_candidate_file`."""
    from taxonomy_pipeline.enrichment.models import PlacementPath, ReviewDecision

    return ReviewDecision(
        members=[{"source_dataset": "case-a", "tag_id": "9"}], action="create", reason="Reviewed",
        approved_paths=[PlacementPath(service="Subject", category="Data analytics", speciality="Fire Prevention")],
    )
