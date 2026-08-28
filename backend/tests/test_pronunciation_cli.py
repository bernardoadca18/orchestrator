from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _run_cli(*arguments: str) -> dict:
    backend = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "scripts/pronunciation.py", *arguments],
        cwd=backend,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_validate_command_reports_stable_identities() -> None:
    result = _run_cli("validate")
    assert result["valid"] is True
    assert result["entry_count"] >= 1
    assert len(result["configuration_identity"]) == 64
    assert len(result["production_identity"]) == 64


def test_resolve_command_is_a_synthesis_free_dry_run() -> None:
    result = _run_cli("resolve", "--text", "A Rockstar Games mostrou GTA 6.")
    assert result["canonical"] == "A Rockstar Games mostrou GTA 6."
    assert result["synthesis"] != result["canonical"]
    assert [replacement["entry_id"] for replacement in result["replacements"]] == [
        "brand.rockstar-games",
        "initialism.gta",
    ]
