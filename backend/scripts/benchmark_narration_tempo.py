"""Build an opt-in, deterministic pacing A/B package from an existing narration WAV."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.narration_postprocessing import (  # noqa: E402
    process_narration_tempo_candidate,
)
from app.services.production_audio import inspect_wav  # noqa: E402


def _atomic_json(path: Path, payload: object) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--factors", nargs="+", type=float, default=[1.0, 1.05, 1.10, 1.15, 1.20, 1.25]
    )
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    source = args.input.resolve(strict=True)
    source_meta = inspect_wav(source)
    factors = tuple(dict.fromkeys(round(value, 4) for value in args.factors))
    if any(not 1.0 <= value <= 1.5 for value in factors):
        parser.error("all factors must be between 1.0 and 1.5")
    print(f"Input: {source}")
    print(f"SHA-256: {source_meta.sha256}")
    print(f"Tempo candidates: {', '.join(f'{value:.2f}x' for value in factors)}")
    if not args.run:
        print("PLAN ONLY: no audio processing. Add --run to create candidates.")
        return 0
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-narration-tempo-v1"
    run_dir = args.output.resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    records = []
    for index, factor in enumerate(factors):
        name = f"{chr(65 + index)}_{factor:.2f}x.wav"
        destination = run_dir / name
        if factor == 1.0:
            shutil.copy2(source, destination)
            if inspect_wav(destination).sha256 != source_meta.sha256:
                raise RuntimeError("raw pacing control checksum mismatch")
            provenance = {
                "tempo_factor": 1.0,
                "input_sha256": source_meta.sha256,
                "output_sha256": source_meta.sha256,
                "control": True,
            }
        else:
            provenance = process_narration_tempo_candidate(source, destination, tempo_factor=factor)
        records.append(
            {
                "display_id": chr(65 + index),
                "tempo_factor": factor,
                "wav": name,
                "provenance": provenance,
            }
        )
    _atomic_json(
        run_dir / "manifest.json",
        {
            "run_id": run_id,
            "created_at": datetime.now(UTC).isoformat(),
            "source_sha256": source_meta.sha256,
            "purpose": "human pacing review only",
            "duration_contract_fix": False,
            "automatically_promoted": False,
            "candidates": records,
        },
    )
    (run_dir / "LISTENING.md").write_text(
        "# Narration pacing A/B\n\n"
        "Compare naturalness, intelligibility, fatigue, prosody and phase artifacts "
        "at matched volume. Tempo is a delivery experiment, never a way to repair "
        "an undersized script. No factor is promoted automatically.\n",
        encoding="utf-8",
    )
    print(f"Completed: {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
