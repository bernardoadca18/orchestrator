from __future__ import annotations

from app.services.content_chunk_diagnostics import analyze_chunk_content
from app.services.content_coverage import ContentCoverageThresholds
from app.services.transcription_service import NarrationWord


def test_timestamped_asr_is_partitioned_without_another_transcription_pass() -> None:
    first = "A primeira parte apresenta o contexto completo."
    second = "O Xbox 360 encerra a missão com os veículos."
    synthesis = first + " " + second
    boundary = len(first) + 1
    metadata = {
        "assembly_timeline": [
            {
                "kind": "generated_chunk",
                "chunk_index": 0,
                "start_seconds": 0.0,
                "end_seconds": 3.0,
                "text_start_char": 0,
                "text_end_char": boundary,
            },
            {
                "kind": "generated_chunk",
                "chunk_index": 1,
                "start_seconds": 3.2,
                "end_seconds": 6.0,
                "text_start_char": boundary,
                "text_end_char": len(synthesis),
            },
        ]
    }
    words = (
        NarrationWord("A primeira parte apresenta o contexto completo", 0.1, 2.8),
        NarrationWord("O ixbox trezentos e sessenta encerra a missão com os vaículos", 3.3, 5.9),
    )

    result = analyze_chunk_content(
        synthesis,
        words,
        metadata,
        thresholds=ContentCoverageThresholds(),
        normalize_hypothesis=lambda value: value,
    )

    assert result["status"] == "available"
    assert result["asr_pass_count"] == 1
    assert result["chunk_count"] == 2
    chunks = result["chunks"]
    assert isinstance(chunks, list)
    assert chunks[0]["classification"] == "content_complete"
    assert chunks[1]["classification"] == "pronunciation_or_asr_disagreement"
    assert chunks[1]["retry_required"] is False


def test_malformed_chunk_timeline_is_diagnostic_not_a_crash() -> None:
    result = analyze_chunk_content(
        "Texto completo.",
        (),
        {"assembly_timeline": [{"kind": "generated_chunk", "start_seconds": "bad"}]},
        thresholds=ContentCoverageThresholds(),
        normalize_hypothesis=lambda value: value,
    )

    assert result == {
        "status": "unavailable",
        "strategy_version": "narration_chunk_content_diagnostics_v1",
        "asr_pass_count": 1,
        "decision_authority": "aggregate_structural_content_report",
        "reason": "provider_chunk_timeline_malformed",
        "chunks": [],
    }
