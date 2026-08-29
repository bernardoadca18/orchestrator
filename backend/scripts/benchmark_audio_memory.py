"""CPU-only long-form audio RSS benchmark with strict dry-plan semantics."""

from __future__ import annotations

import argparse
import json
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from app.services.audio_diagnostics import DIAGNOSTICS_VERSION, analyze_audio
from app.services.mastering import MasteringConfig, master_narration
from app.services.production_audio import assemble_narration

BENCHMARK_VERSION = "audio_memory_benchmark_v1"
OPERATIONS = ("assembly", "diagnostics", "mastering")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation", choices=(*OPERATIONS, "all"), default="all")
    parser.add_argument("--minutes", type=int, nargs="+", default=[10, 60])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--_case", nargs=2, metavar=("OPERATION", "MINUTES"), help=argparse.SUPPRESS)
    return parser


def _write_repeated_source(path: Path, *, minutes: int, diagnostics: bool = False) -> None:
    sample_rate = 48_000
    seconds = 10
    phase = np.arange(sample_rate * seconds, dtype=np.float64) / sample_rate
    if diagnostics:
        chunk = np.concatenate(
            [
                0.1 * np.sin(2 * np.pi * 220 * phase[: sample_rate * 9]),
                np.zeros(sample_rate, dtype=np.float64),
            ]
        )
    else:
        chunk = 0.1 * np.sin(2 * np.pi * 220 * phase)
    with sf.SoundFile(
        str(path),
        mode="w",
        samplerate=sample_rate,
        channels=1,
        format="WAV",
        subtype="PCM_16",
    ) as output:
        for _ in range(minutes * 6):
            output.write(chunk)


def _write_short_block(path: Path) -> None:
    sample_rate = 48_000
    seconds = 10
    phase = np.arange(sample_rate * seconds, dtype=np.float64) / sample_rate
    sf.write(
        str(path),
        0.1 * np.sin(2 * np.pi * 220 * phase),
        sample_rate,
        format="WAV",
        subtype="PCM_16",
    )


def _run_case(operation: str, minutes: int) -> dict[str, object]:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix=f"neovids-{operation}-memory-") as raw:
        root = Path(raw)
        source = root / "source.wav"
        output = root / "output.wav"
        if operation == "assembly":
            _write_short_block(source)
            result = assemble_narration(
                [source] * (minutes * 6),
                output,
                sample_rate=48_000,
                silence_ms=0,
            )
            output_size = result.metadata.byte_size
            implementation = "orchestrator_streaming_assembly_v1"
        elif operation == "diagnostics":
            _write_repeated_source(source, minutes=minutes, diagnostics=True)
            result = analyze_audio(source)
            output_size = source.stat().st_size
            implementation = str(result["version"])
        elif operation == "mastering":
            _write_repeated_source(source, minutes=minutes)
            result = master_narration(source, output, MasteringConfig())
            output_size = result.metadata.byte_size
            implementation = MasteringConfig().version
        else:
            raise ValueError("unsupported benchmark operation")
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "benchmark_version": BENCHMARK_VERSION,
        "operation": operation,
        "input_minutes": minutes,
        "wall_seconds": round(time.monotonic() - started, 6),
        "peak_rss_kib": int(usage.ru_maxrss),
        "output_size_bytes": output_size,
        "success": True,
        "implementation": implementation,
        "rss_scope": "isolated_child_process_ru_maxrss",
        "gpu_vram": "not_used",
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args._case:
        operation, raw_minutes = args._case
        print(json.dumps(_run_case(operation, int(raw_minutes)), sort_keys=True))
        return 0
    if any(minutes <= 0 or minutes > 120 for minutes in args.minutes):
        raise SystemExit("--minutes values must be between 1 and 120")
    operations = OPERATIONS if args.operation == "all" else (args.operation,)
    plan = {
        "benchmark_version": BENCHMARK_VERSION,
        "mode": "run" if args.run else "plan",
        "operations": list(operations),
        "minutes": args.minutes,
        "cases": len(operations) * len(args.minutes),
        "cpu_only": True,
        "cuda": False,
        "diagnostics_version": DIAGNOSTICS_VERSION,
        "output": str(args.output) if args.output else None,
    }
    if not args.run:
        print(json.dumps(plan, indent=2, sort_keys=True))
        print("PLAN ONLY: no synthetic WAV or benchmark subprocess was created.")
        return 0
    records: list[dict[str, object]] = []
    for operation in operations:
        for minutes in args.minutes:
            process = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--_case",
                    operation,
                    str(minutes),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if process.returncode != 0:
                raise SystemExit(
                    f"{operation}/{minutes}min failed: {process.stderr.strip() or 'unknown error'}"
                )
            record = json.loads(process.stdout.strip().splitlines()[-1])
            records.append(record)
            print(json.dumps(record, sort_keys=True))
    report = {"plan": plan, "results": records}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_name(f".{args.output.name}.tmp")
        temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
