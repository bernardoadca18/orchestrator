from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from scripts.benchmark_clone_modes import build_parser


def test_benchmark_defaults_to_portuguese_asr_language(tmp_path: Path) -> None:
    args = build_parser().parse_args(["--output", str(tmp_path)])

    assert args.asr_language == "pt"


def test_benchmark_script_help_works_when_executed_directly() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    script = backend_root / "scripts" / "benchmark_clone_modes.py"
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)

    completed = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=backend_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--asr-language" in completed.stdout
