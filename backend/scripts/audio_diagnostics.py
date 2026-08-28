"""Print a non-destructive JSON diagnostic report for one PCM16 WAV."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.audio_diagnostics import analyze_audio


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", type=Path)
    parser.add_argument("--silence-threshold-dbfs", type=float, default=-45.0)
    parser.add_argument("--minimum-silence-seconds", type=float, default=0.15)
    args = parser.parse_args()
    report = analyze_audio(
        args.wav,
        silence_threshold_dbfs=args.silence_threshold_dbfs,
        minimum_silence_seconds=args.minimum_silence_seconds,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
