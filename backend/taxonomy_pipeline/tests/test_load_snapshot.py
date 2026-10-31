"""The Docker snapshot loader (`backend/loader/load_snapshot.sh`) against integrate's own snapshot format.

The script runs here with a stand-in `mysql` on PATH that records the session
it is sent, so these tests check what the loader would run - order, lock,
labels, counts, refusals - without a database. The snapshot's enriched.yaml is
written by integrate's `_config_yaml`, so a change to that format that the
loader's parsing can't follow fails here.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from taxonomy_pipeline.enrichment.integrate import _config_yaml

LOADER = Path(__file__).resolve().parents[2] / "loader" / "load_snapshot.sh"
FILES = ["services.sql", "categories.sql", "specialities.sql", "tags.sql", "speciality_tags.sql"]
FAKE_MYSQL = """#!/bin/sh
cat > "$FAKE_SESSION"
if [ -n "${FAKE_MYSQL_ERROR:-}" ]; then
    printf '%s\\n' "$FAKE_MYSQL_ERROR" >&2
    exit 1
fi
"""

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")


def _snapshot(root: Path, name: str = "round1", table_map: dict[str, str] | None = None) -> Path:
    """A snapshot folder shaped like `integrate`'s: baseline copies, additions.sql and enriched.yaml."""
    folder = root / name
    (folder / "baseline").mkdir(parents=True)
    copies = []
    for file in FILES:
        copy = folder / "baseline" / file
        copy.write_text(f"-- {file}\n", encoding="utf-8")
        copies.append(copy)
    (folder / "additions.sql").write_text("-- additions\n", encoding="utf-8")
    yaml_text = _config_yaml(copies, folder, table_map or {}, "original", None)
    (folder / "enriched.yaml").write_text(yaml_text, encoding="utf-8")
    return folder


def _run(tmp_path: Path, *args: str, **env: str) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run the loader with the stand-in mysql; returns the process and the SQL session it sent."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "mysql").write_text(FAKE_MYSQL, encoding="utf-8")
    (bin_dir / "mysql").chmod(0o755)
    session = tmp_path / "session.sql"
    session.unlink(missing_ok=True)
    process = subprocess.run(
        ["sh", str(LOADER), *args], capture_output=True, text=True, check=False,
        env={"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "SNAPSHOTS_DIR": str(tmp_path / "snapshots"),
             "FAKE_SESSION": str(session), **env},
    )
    return process, session.read_text(encoding="utf-8") if session.exists() else ""


def test_rebuilds_the_target_from_the_snapshot_files_in_enriched_yaml_order(tmp_path: Path) -> None:
    _snapshot(tmp_path / "snapshots")
    process, session = _run(tmp_path, "round1")

    assert process.returncode == 0, process.stderr
    assert "'taxonomy_latest' now holds snapshot 'round1'" in process.stdout
    lock, drop, create, use = session.splitlines()[:4]
    assert lock == "DO IF(GET_LOCK('taxonomy_loader.taxonomy_latest', 0) = 1, 0, (SELECT 1 UNION ALL SELECT 2));"
    assert (drop, use) == ("DROP DATABASE IF EXISTS `taxonomy_latest`;", "USE `taxonomy_latest`;")
    assert create.startswith("CREATE DATABASE `taxonomy_latest`")
    loaded = [line for line in session.splitlines() if line.startswith("-- ")]
    assert loaded == [f"-- {file}" for file in FILES] + ["-- additions"]
    assert "INSERT INTO _snapshot (name) VALUES ('round1');" in session
    assert "COUNT(*)) FROM `tags`;" in session


def test_counts_read_the_snapshots_own_table_names(tmp_path: Path) -> None:
    _snapshot(tmp_path / "snapshots", table_map={"tags": "sf_tags", "categories": "sf_categories"})
    process, session = _run(tmp_path, "round1")

    assert process.returncode == 0, process.stderr
    assert "COUNT(*)) FROM `sf_tags`;" in session and "COUNT(*)) FROM `sf_categories`;" in session
    assert "COUNT(*)) FROM `specialities`;" in session


@pytest.mark.parametrize("target", ["greygigz_beta", "mysql", "information_schema"])
def test_refuses_the_original_and_mysqls_own_databases(tmp_path: Path, target: str) -> None:
    _snapshot(tmp_path / "snapshots")
    process, session = _run(tmp_path, "round1", TARGET_DATABASE=target)
    assert process.returncode == 2 and "it is a protected database" in process.stderr
    assert session == ""


@pytest.mark.parametrize(("args", "env", "message"), [
    ((), {}, "usage:"),
    (("missing",), {}, "no snapshot 'missing'"),
    (("o'connor",), {}, "unsupported snapshot name"),
    (("round1",), {"TARGET_DATABASE": "x;drop"}, "invalid TARGET_DATABASE"),
])
def test_refuses_bad_input_before_running_any_sql(
    tmp_path: Path, args: tuple[str, ...], env: dict[str, str], message: str,
) -> None:
    _snapshot(tmp_path / "snapshots")
    process, session = _run(tmp_path, *args, **env)
    assert process.returncode == 2 and message in process.stderr
    assert session == ""


def test_an_unknown_snapshot_lists_the_available_ones(tmp_path: Path) -> None:
    _snapshot(tmp_path / "snapshots", name="judged-1015")
    process, _ = _run(tmp_path, "nope")
    assert "available snapshots:\n  judged-1015" in process.stderr


def test_refuses_a_file_list_that_is_missing_a_file_or_does_not_end_with_additions(tmp_path: Path) -> None:
    folder = _snapshot(tmp_path / "snapshots")
    (folder / "baseline" / "tags.sql").unlink()
    process, session = _run(tmp_path, "round1")
    assert process.returncode == 1 and "lists baseline/tags.sql, which is missing" in process.stderr
    assert session == ""


def test_reports_another_loader_holding_the_lock_without_calling_it_partial(tmp_path: Path) -> None:
    _snapshot(tmp_path / "snapshots")
    process, _ = _run(tmp_path, "round1",
                      FAKE_MYSQL_ERROR="ERROR 1242 (21000) at line 1: Subquery returns more than 1 row")
    assert process.returncode == 1
    assert "another loader is rebuilding 'taxonomy_latest' right now; nothing was changed" in process.stderr


def test_a_failing_file_reports_the_target_as_partial(tmp_path: Path) -> None:
    _snapshot(tmp_path / "snapshots")
    process, _ = _run(tmp_path, "round1", FAKE_MYSQL_ERROR="ERROR 1062 (23000) at line 40: Duplicate entry")
    assert process.returncode == 1
    assert "Duplicate entry" in process.stderr and "'taxonomy_latest' is now partial" in process.stderr
