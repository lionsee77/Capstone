from __future__ import annotations

import yaml

from taxonomy_pipeline.cli import main
from tests.conftest import FIXTURES


def _write_config(tmp_path, metrics):
    config = {
        "datasets": [{"name": "original", "paths": [str(FIXTURES / "mini_original.sql")]}],
        "metrics": metrics,
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config))
    return config_path


def test_exit_code_zero_when_every_metric_succeeds(tmp_path, isolated_db, capsys):
    config_path = _write_config(tmp_path, [{"id": "integrity", "params": {"dataset": "original"}}])
    code = main(["run", "--config", str(config_path)])
    assert code == 0


def test_exit_code_nonzero_when_a_metric_errors(tmp_path, isolated_db, capsys):
    # valid pipeline run (report.status == "done"), but one metric id doesn't
    # exist - this must still fail the CLI, not just log a warning.
    config_path = _write_config(
        tmp_path,
        [
            {"id": "integrity", "params": {"dataset": "original"}},
            {"id": "not_a_real_metric"},
        ],
    )
    code = main(["run", "--config", str(config_path)])
    assert code != 0
