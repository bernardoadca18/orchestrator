from pathlib import Path
from unittest.mock import patch

from scripts.smoke_production_e2e import build_parser, run


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
