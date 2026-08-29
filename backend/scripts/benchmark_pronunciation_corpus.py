#!/usr/bin/env python3
# ruff: noqa: E402, E501
"""Feed the versioned pronunciation corpus into the existing Pronunciation Lab.

This helper is intentionally separate from reference regression.  It requires a
selected registered profile and never writes the production lexicon or promotes a
candidate.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.clients.voice_client import VoiceClient
from app.core.config import Settings
from app.services.pronunciation_lab import (
    LabCandidate,
    LabSpec,
    PronunciationLabRunner,
    VoiceClientLabGateway,
)
from app.services.reference_benchmark import (
    DEFAULT_PRONUNCIATION_CORPUS_PATH,
    load_pronunciation_corpus,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_PRONUNCIATION_CORPUS_PATH)
    parser.add_argument("--voice-profile", required=True)
    parser.add_argument("--generation-profile", default="narration_v1")
    parser.add_argument(
        "--output", type=Path, default=Path("/data/audio/benchmarks/pronunciation-lab")
    )
    parser.add_argument("--seeds", nargs="+", type=int, default=[101])
    parser.add_argument("--target")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args(argv)
    corpus = load_pronunciation_corpus(args.corpus)
    targets = [
        target
        for target in corpus["targets"]
        if args.target is None or target["term"] == args.target
    ]
    if args.target and not targets:
        parser.error(f"unknown pronunciation target: {args.target}")
    cells = sum(len(target["contexts"]) * len(args.seeds) for target in targets)
    print(
        f"Pronunciation corpus: {corpus['version']}; targets: {len(targets)}; planned cells: {cells}"
    )
    print(
        f"Selected voice profile: {args.voice_profile}; generation profile: {args.generation_profile}"
    )
    if not args.run:
        print(
            "PLAN ONLY: no Qwen synthesis and no production lexicon mutation (pass --run to execute)"
        )
        return 0

    async def execute() -> None:
        settings = Settings(_env_file=None)
        async with VoiceClient(settings) as client:
            gateway = VoiceClientLabGateway(client)
            runner = PronunciationLabRunner(gateway)
            for target in targets:
                candidate = LabCandidate(
                    id="manual.reference-term",
                    text=target["term"],
                    strategy="manual",
                    strategy_version="manual_v1",
                )
                spec = LabSpec(
                    target_term=target["term"],
                    contexts=tuple(target["contexts"]),
                    candidates=(candidate,),
                    language="Portuguese",
                    locale="pt-BR",
                    voice_profile=args.voice_profile,
                    generation_profile=args.generation_profile,
                    seeds=tuple(args.seeds),
                )
                summary = await runner.run(spec, output_root=args.output)
                print(f"{target['term']}: {summary.run_directory}")

    asyncio.run(execute())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
