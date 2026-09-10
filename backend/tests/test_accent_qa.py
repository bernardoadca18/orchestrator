from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from app.services import accent_qa
from app.services.accent_qa import (
    FEATURE_NAMES,
    FEATURE_VERSION,
    MODEL_SCHEMA,
    AccentDetector,
    AccentPrototypeModel,
    aggregate_accent_diagnostics,
    extract_accent_features,
    feature_identity,
)
from scripts.accent_corpus import _parse_chunk_labels


def _model(path: Path, *, likely_threshold: float = 0.75) -> Path:
    dimensions = len(FEATURE_NAMES)
    payload = {
        "schema": MODEL_SCHEMA,
        "model_version": "fixture-v1",
        "feature_version": FEATURE_VERSION,
        "feature_names": list(FEATURE_NAMES),
        "normalization_mean": [0.0] * dimensions,
        "normalization_scale": [1.0] * dimensions,
        "pt_br_centroid": [0.0] * dimensions,
        "pt_pt_centroid": [1.0] * dimensions,
        "temperature": 0.1,
        "pt_br_confident_max_pt_pt_score": 0.25,
        "likely_pt_pt_min_score": likely_threshold,
        "training_manifest_identity": "fixture",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _features(value: float) -> dict[str, float]:
    return dict.fromkeys(FEATURE_NAMES, value)


def test_feature_extraction_is_deterministic_and_bounded(tmp_path: Path) -> None:
    sample_rate = 48_000
    timeline = np.arange(sample_rate * 2, dtype=np.float64) / sample_rate
    source = tmp_path / "voice.wav"
    sf.write(source, 0.1 * np.sin(2 * np.pi * 190 * timeline), sample_rate)

    first = extract_accent_features(source)
    second = extract_accent_features(source)

    assert first == second
    assert tuple(first) == FEATURE_NAMES
    assert all(np.isfinite(list(first.values())))


@pytest.mark.parametrize(
    ("value", "classification"),
    [(0.0, "pt_br_confident"), (0.5, "uncertain"), (1.0, "likely_pt_pt")],
)
def test_three_state_classifier(value: float, classification: str, tmp_path: Path) -> None:
    model = AccentPrototypeModel.load(_model(tmp_path / "model.json"))
    actual, pt_br_score, pt_pt_score = model.classify(_features(value))
    assert actual == classification
    assert pt_br_score + pt_pt_score == pytest.approx(1.0)


def test_model_and_feature_identities_are_deterministic(tmp_path: Path) -> None:
    first = AccentPrototypeModel.load(_model(tmp_path / "first.json"))
    second = AccentPrototypeModel.load(_model(tmp_path / "second.json"))
    changed = AccentPrototypeModel.load(_model(tmp_path / "changed.json", likely_threshold=0.8))
    assert first.identity == second.identity
    assert first.identity != changed.identity
    assert feature_identity() == feature_identity()


def test_missing_or_corrupt_audio_is_diagnostic(monkeypatch, tmp_path: Path) -> None:
    detector = AccentDetector(_model(tmp_path / "model.json"), "shadow")
    missing = detector.analyze(tmp_path / "missing.wav")
    assert missing["classification"] == "uncertain"
    assert missing["detector_status"] == "unavailable"
    assert missing["blocks_promotion"] is False

    corrupt = tmp_path / "corrupt.wav"
    corrupt.write_bytes(b"not wav")
    assert detector.analyze(corrupt)["reason_code"] == "accent_detector_unavailable"


def test_detector_unavailable_shadow_does_not_block_but_enforce_does(tmp_path: Path) -> None:
    audio = tmp_path / "unused.wav"
    shadow = AccentDetector(tmp_path / "missing-model.json", "shadow").analyze(audio)
    enforce = AccentDetector(tmp_path / "missing-model.json", "enforce").analyze(audio)
    assert shadow["retry_required"] is False
    assert shadow["blocks_promotion"] is False
    assert enforce["retry_required"] is False
    assert enforce["blocks_promotion"] is True


def test_shadow_and_enforce_apply_policy_without_speaker_or_content_coupling(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(accent_qa, "extract_accent_features", lambda _: _features(1.0))
    model = _model(tmp_path / "model.json")
    shadow = AccentDetector(model, "shadow").analyze(tmp_path / "audio.wav")
    enforce = AccentDetector(model, "enforce").analyze(tmp_path / "audio.wav")
    assert shadow["classification"] == enforce["classification"] == "likely_pt_pt"
    assert shadow["retry_required"] is False
    assert enforce["retry_required"] is True
    assert "speaker" not in shadow
    assert "content" not in shadow


def test_aggregate_diagnostics_preserves_chunk_reason_codes() -> None:
    items = [
        {"classification": "pt_br_confident", "detector_status": "available"},
        {"classification": "likely_pt_pt", "detector_status": "available"},
    ]
    shadow = aggregate_accent_diagnostics(items, "shadow")
    enforce = aggregate_accent_diagnostics(items, "enforce")
    assert shadow["likely_pt_pt_chunk_indexes"] == [1]
    assert shadow["blocks_promotion"] is False
    assert enforce["retry_required"] is True
    assert enforce["reason_code"] == "accent_likely_pt_pt"


def test_corpus_accepts_explicit_chunk_labels_without_inventing_job_labels() -> None:
    labels = _parse_chunk_labels(["job-1:7=PT_PT_BAD", "job-1:8=UNCERTAIN"])
    assert labels == {("job-1", 7): "PT_PT_BAD", ("job-1", 8): "UNCERTAIN"}
    with pytest.raises(ValueError, match="invalid --chunk-label"):
        _parse_chunk_labels(["job-1:7=PROBABLY_PT_PT"])
