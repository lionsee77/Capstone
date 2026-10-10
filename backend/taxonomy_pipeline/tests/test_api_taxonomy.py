from __future__ import annotations

import time

from fastapi.testclient import TestClient

from taxonomy_pipeline.api.app import app
from tests.conftest import FIXTURES


def _config_payload() -> dict:
    return {
        "datasets": [
            {"name": "original", "paths": [str(FIXTURES / "mini_original.sql")]},
        ],
        "metrics": [
            {"id": "integrity", "params": {"dataset": "original"}},
        ],
    }


def _run_id(client: TestClient) -> str:
    created = client.post("/runs", json=_config_payload())
    run_id = created.json()["run_id"]
    for _ in range(20):
        if client.get(f"/runs/{run_id}").json()["status"] in ("done", "failed"):
            break
        time.sleep(0.05)
    return run_id


def test_search_matches_by_name_case_and_punctuation_insensitively(isolated_db):
    client = TestClient(app)
    run_id = _run_id(client)

    response = client.get("/taxonomy/search", params={"run_id": run_id, "dataset": "original", "q": "TAX"})

    assert response.status_code == 200
    results = response.json()
    assert {(r["level"], r["name"]) for r in results} == {("specialities", "Tax Advisory"), ("tags", "tax")}


def test_search_filters_by_level(isolated_db):
    client = TestClient(app)
    run_id = _run_id(client)

    response = client.get(
        "/taxonomy/search", params={"run_id": run_id, "dataset": "original", "q": "a", "level": "categories"},
    )

    assert response.status_code == 200
    results = response.json()
    assert {r["level"] for r in results} == {"categories"}
    assert {r["name"] for r in results} == {"Accounting", "accounting", "Marketing"}


def test_search_with_no_query_browses_up_to_the_limit(isolated_db):
    client = TestClient(app)
    run_id = _run_id(client)

    response = client.get(
        "/taxonomy/search", params={"run_id": run_id, "dataset": "original", "level": "categories", "limit": 2},
    )

    assert response.status_code == 200
    assert len(response.json()) == 2


def test_search_rejects_an_unknown_level(isolated_db):
    client = TestClient(app)
    run_id = _run_id(client)

    response = client.get(
        "/taxonomy/search", params={"run_id": run_id, "dataset": "original", "level": "not-a-level"},
    )

    assert response.status_code == 422


def test_search_against_an_unknown_run_is_404(isolated_db):
    client = TestClient(app)

    response = client.get("/taxonomy/search", params={"run_id": "does-not-exist", "dataset": "original"})

    assert response.status_code == 404


def test_search_against_an_unknown_dataset_name_is_404(isolated_db):
    client = TestClient(app)
    run_id = _run_id(client)

    response = client.get("/taxonomy/search", params={"run_id": run_id, "dataset": "no-such-dataset"})

    assert response.status_code == 404


def test_concept_detail_resolves_the_full_path_and_tags_for_a_speciality(isolated_db):
    client = TestClient(app)
    run_id = _run_id(client)

    response = client.get("/taxonomy/specialities/10", params={"run_id": run_id, "dataset": "original"})

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Tax Advisory"
    assert len(body["paths"]) == 1
    path = body["paths"][0]
    assert path["category"] == {"id": 1, "name": "Accounting"}
    assert path["speciality"] == {"id": 10, "name": "Tax Advisory"}
    assert {t["name"] for t in body["tags"]} == {"tax", "gst"}
    assert body["children"] == []


def test_concept_detail_for_a_category_lists_its_specialities_as_children(isolated_db):
    client = TestClient(app)
    run_id = _run_id(client)

    response = client.get("/taxonomy/categories/1", params={"run_id": run_id, "dataset": "original"})

    assert response.status_code == 200
    body = response.json()
    assert body["paths"] == [{"service": None, "category": {"id": 1, "name": "Accounting"}, "speciality": None}]
    assert {c["name"] for c in body["children"]} == {"Tax Advisory"}
    assert body["tags"] == []


def test_concept_detail_for_a_tag_lists_one_path_per_linked_speciality_and_skips_dangling_links(isolated_db):
    # Tag 100 is linked to speciality 10 (real) and speciality 999 (dangling,
    # no such speciality) - only the real link should produce a path.
    client = TestClient(app)
    run_id = _run_id(client)

    response = client.get("/taxonomy/tags/100", params={"run_id": run_id, "dataset": "original"})

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "tax"
    assert len(body["paths"]) == 1
    assert body["paths"][0]["speciality"] == {"id": 10, "name": "Tax Advisory"}


def test_concept_detail_for_an_orphan_speciality_has_a_short_path_not_an_error(isolated_db):
    # Speciality 12's category_id (999) doesn't resolve - same row
    # integrity.py reports as an orphan speciality.
    client = TestClient(app)
    run_id = _run_id(client)

    response = client.get("/taxonomy/specialities/12", params={"run_id": run_id, "dataset": "original"})

    assert response.status_code == 200
    body = response.json()
    assert body["paths"] == [{"service": None, "category": None, "speciality": {"id": 12, "name": "Orphan Speciality"}}]


def test_concept_detail_for_an_unknown_id_is_404(isolated_db):
    client = TestClient(app)
    run_id = _run_id(client)

    response = client.get("/taxonomy/tags/99999", params={"run_id": run_id, "dataset": "original"})

    assert response.status_code == 404
