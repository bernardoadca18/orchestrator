"""Create a small, opt-in narration-stem A/B/C listening package."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from app.services.narration_postprocessing import (
    BALANCED,
    CONSERVATIVE,
    process_narration_stem,
)
from app.services.production_audio import inspect_wav


def _atomic_json(path: Path, payload: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run", action="store_true", help="Actually process audio")
    args = parser.parse_args()
    source = args.input.resolve(strict=True)
    source_metadata = inspect_wav(source)
    print(f"Input: {source}")
    print(f"SHA-256: {source_metadata.sha256}")
    print("Candidates: A raw, B conservative (-17 LUFS), C balanced (-16 LUFS)")
    print(f"Output: {args.output}")
    if not args.run:
        print("PLAN ONLY: zero audio processing performed. Add --run to execute.")
        return 0

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-narration-post-v1"
    run_dir = args.output.resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    raw_copy = run_dir / "A_raw.wav"
    shutil.copy2(source, raw_copy)
    if inspect_wav(raw_copy).sha256 != source_metadata.sha256:
        raise RuntimeError("raw A/B copy checksum mismatch")
    candidates = [
        {"display_id": "A", "profile": "raw", "wav": raw_copy.name},
        {
            "display_id": "B",
            "profile": CONSERVATIVE.name,
            "wav": "B_conservative.wav",
            "provenance": process_narration_stem(
                source, run_dir / "B_conservative.wav", CONSERVATIVE
            ),
        },
        {
            "display_id": "C",
            "profile": BALANCED.name,
            "wav": "C_balanced.wav",
            "provenance": process_narration_stem(
                source, run_dir / "C_balanced.wav", BALANCED
            ),
        },
    ]
    manifest = {
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "purpose": "human narration-stem post-processing A/B review",
        "not_final_program_master": True,
        "source_sha256": source_metadata.sha256,
        "candidates": candidates,
    }
    _atomic_json(run_dir / "manifest.json", manifest)
    (run_dir / "LISTENING.md").write_text(
        "# Narration stem A/B/C\n\n"
        "Use matched playback volume. Compare naturalness, clarity, fatigue, pumping, "
        "sibilance and retained prosody. A is raw. B is conservative. C is balanced. "
        "Higher loudness alone is not a preference signal. No candidate is promoted "
        "automatically; this is not final-program mastering.\n",
        encoding="utf-8",
    )
    print(f"Completed: {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
