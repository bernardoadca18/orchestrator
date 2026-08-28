"""Opt-in real-model ICL vs x-vector benchmark with ASR and signal diagnostics."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.clients.voice_client import VoiceClient, VoiceJobFailedError
from app.core.config import Settings
from app.services.audio_diagnostics import analyze_audio
from app.services.content_coverage import ContentCoverageThresholds, analyze_content_coverage
from app.services.production_audio import publish_source_block
from app.services.pronunciation import PronunciationPreprocessor
from app.services.text_normalization import PortugueseTextNormalizer
from app.services.transcription_service import FasterWhisperNarrationProvider

DEFAULT_CASES = (
    ("natural_ptbr", "A", "Hoje vamos entender como uma boa decisão simplifica todo o processo."),
    (
        "long_form",
        "B",
        "Em uma produção longa, cada etapa precisa preservar o texto, a identidade da voz e "
        "a integridade do arquivo. Primeiro validamos as entradas; depois acompanhamos a geração, "
        "medimos o resultado físico e só então liberamos o áudio para as etapas seguintes.",
    ),
    (
        "punctuation_heavy",
        "C",
        "A pergunta é simples: funcionou? Sim — mas há três cuidados: ritmo, clareza "
        "e consistência.",
    ),
    (
        "mixed_technical",
        "D",
        "O pipeline usa Qwen, HTTP Range, checksum SHA-256 e um workflow em PostgreSQL.",
    ),
    (
        "pronunciation_lab",
        "E",
        "Geralt usa Aard enquanto o HUD registra o combate em Kaer Morhen.",
    ),
    (
        "sentence_skip_regression",
        "F",
        "A primeira frase apresenta o contexto completo. A segunda explica o problema sem omitir "
        "nenhuma informação. A terceira confirma a conclusão esperada.",
    ),
    (
        "runaway_regression",
        "G",
        "Este caso verifica que um parágrafo comum termina normalmente, sem repetir frases, sem "
        "abandonar a ideia no meio e sem continuar gerando conteúdo além do texto solicitado.",
    ),
)


def _thresholds(config: Settings) -> ContentCoverageThresholds:
    return ContentCoverageThresholds(
        minimum_coverage_ratio=config.audio_content_minimum_coverage_ratio,
        maximum_deletion_ratio=config.audio_content_maximum_deletion_ratio,
        maximum_insertion_ratio=config.audio_content_maximum_insertion_ratio,
        maximum_repetition_ratio=config.audio_content_maximum_repetition_ratio,
        maximum_missing_contiguous_tokens=config.audio_content_maximum_missing_tokens,
        maximum_trailing_missing_tokens=config.audio_content_maximum_trailing_missing_tokens,
    )


async def run(args: argparse.Namespace) -> int:
    config = Settings(_env_file=args.env_file)
    normalizer = PortugueseTextNormalizer(version=config.text_normalizer_version)
    pronunciation = PronunciationPreprocessor.load(config.pronunciation_glossary_path)
    transcription = FasterWhisperNarrationProvider(config)
    run_id = args.run_id or uuid4().hex
    run_output = args.output / run_id
    run_output.mkdir(parents=True, exist_ok=True)
    manifest_path = run_output / "manifest.jsonl"
    records: list[dict[str, Any]] = []
    async with VoiceClient(config) as voice:
        for name, category, canonical_text in DEFAULT_CASES:
            normalized = normalizer.normalize(canonical_text)
            resolved = pronunciation.preprocess(
                normalized.normalized_text,
                project="clone-mode-benchmark",
                locale=config.pronunciation_locale,
                canonical_text=canonical_text,
            )
            text_identity = hashlib.sha256(resolved.synthesis_text.encode()).hexdigest()
            for profile in args.voices:
                started = time.monotonic()
                key = (
                    f"clone-mode-benchmark:{run_id}:{profile}:{args.generation}:"
                    f"{args.seed}:{text_identity}"
                )
                created = await voice.create_narration(
                    text=resolved.synthesis_text,
                    voice_profile=profile,
                    generation_profile=args.generation,
                    idempotency_key=key,
                    language="Portuguese",
                    seed=args.seed,
                )
                try:
                    completed = await voice.wait_for_narration(created.id)
                except VoiceJobFailedError as exc:
                    records.append(
                        {
                            "case": name,
                            "category": category,
                            "voice_profile": profile,
                            "benchmark_run_id": run_id,
                            "status": "failed",
                            "error_category": exc.category,
                            "error_message": exc.public_message,
                            "text_identity": text_identity,
                        }
                    )
                    continue
                payload = await voice.download_audio(completed.id)
                wav_path = run_output / f"{category}-{name}--{profile}--seed-{args.seed}.wav"
                physical = publish_source_block(
                    payload,
                    wav_path,
                    expected_sha256=completed.result_sha256,
                    expected_byte_size=completed.result_byte_size,
                    expected_duration_seconds=completed.result_duration_seconds,
                    expected_sample_rate=completed.result_sample_rate,
                    expected_channels=completed.result_channels,
                    expected_subtype=completed.result_subtype,
                    expected_frame_count=completed.result_frame_count,
                    expected_riff_data_byte_size=completed.result_riff_data_byte_size,
                )
                asr = transcription.transcribe(wav_path)
                coverage = analyze_content_coverage(
                    resolved.synthesis_text,
                    asr.text,
                    thresholds=_thresholds(config),
                )
                signal = analyze_audio(wav_path)
                elapsed = time.monotonic() - started
                records.append(
                    {
                        "case": name,
                        "category": category,
                        "voice_profile": profile,
                        "benchmark_run_id": run_id,
                        "voice_identity": completed.metadata.get("voice_identity"),
                        "clone_mode": completed.metadata.get("voice_clone_mode"),
                        "generation_profile": args.generation,
                        "generation_identity": completed.metadata.get("generation_identity"),
                        "model_id": completed.model_id,
                        "model_revision": completed.model_revision,
                        "seed": completed.resolved_seed,
                        "status": "completed",
                        "text_identity": text_identity,
                        "normalizer_identity": normalized.identity,
                        "pronunciation_identity": resolved.glossary_identity,
                        "pronunciation_replacements": list(resolved.applied_replacements),
                        "duration_seconds": physical.duration_seconds,
                        "wall_clock_seconds": elapsed,
                        "real_time_factor": completed.metadata.get(
                            "real_time_factor", elapsed / physical.duration_seconds
                        ),
                        "artifact": {
                            "sha256": physical.sha256,
                            "byte_size": physical.byte_size,
                            "frame_count": physical.frame_count,
                            "sample_rate": physical.sample_rate,
                        },
                        "content_coverage": coverage.to_dict(),
                        "asr": {
                            "transcript": asr.text,
                            "provider": asr.provider,
                            "model": asr.model,
                            "language": asr.language,
                        },
                        "signal_diagnostics": signal,
                        "qwen_chunks": completed.metadata.get("chunks", []),
                        "quality_guard_status": completed.metadata.get(
                            "quality_guard_status", "passed"
                        ),
                        "human_review": {
                            "timbre_similarity": None,
                            "naturality": None,
                            "pace": None,
                            "accent": None,
                            "pause_quality": None,
                            "pronunciation": None,
                            "notes": None,
                        },
                    }
                )
    manifest_path.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records
        ),
        encoding="utf-8",
    )
    print(manifest_path)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--voices", nargs=2, default=["bernardo_v1", "bernardo_xvec_v1"])
    parser.add_argument("--generation", default="narration_v1")
    parser.add_argument("--seed", type=int, default=12345)
    parser.add_argument(
        "--run-id",
        help="Optional idempotency scope; omitted creates a fresh real generation run",
    )
    parser.add_argument("--output", type=Path, required=True)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
