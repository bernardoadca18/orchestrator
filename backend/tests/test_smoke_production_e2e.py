from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.smoke_production_e2e import _assert_visual_contract, build_parser, run


def test_e2e_smoke_is_strict_plan_without_run(tmp_path: Path, capsys):
    args = build_parser().parse_args(
        [
            "--workflow-id",
            "00000000-0000-0000-0000-000000000001",
            "--report",
            str(tmp_path / "report.json"),
        ]
    )

    with patch("scripts.smoke_production_e2e.httpx.Client") as client:
        assert run(args) == 0

    client.assert_not_called()
    assert not (tmp_path / "report.json").exists()
    assert "PLAN ONLY" in capsys.readouterr().out


def test_e2e_smoke_rejects_completed_zero_query_visual_plan() -> None:
    with pytest.raises(RuntimeError, match="visual_queries_missing"):
        _assert_visual_contract(
            {"duracao_audio_segundos": 125.0},
            {"status": "completed", "total_queries": 0},
        )

    _assert_visual_contract(
        {"duracao_audio_segundos": 125.0},
        {"status": "completed_with_gaps", "total_queries": 5},
    )
