"""Lazy, bounded acoustic accent QA with an auditable three-state policy.

The detector deliberately does not use ASR text or speaker similarity.  Its
small acoustic feature vector is suitable for a linear/prototype model trained
outside the production request path.  A missing model is a first-class,
persisted diagnostic rather than an implicit pass.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import soundfile as sf
from scipy.fft import dct
from scipy.signal import resample_poly

AccentMode = Literal["off", "shadow", "enforce"]
AccentClassification = Literal["pt_br_confident", "uncertain", "likely_pt_pt"]

FEATURE_VERSION = "pt_locale_mfcc_prosody_v1"
POLICY_VERSION = "pt_br_accent_three_state_v1"
MODEL_SCHEMA = "neovids_accent_prototype_model_v1"
TARGET_SAMPLE_RATE = 16_000
MAX_ANALYSIS_SECONDS = 30.0
FEATURE_NAMES = tuple(
    [f"mfcc_{index:02d}_mean" for index in range(13)]
    + [f"mfcc_{index:02d}_std" for index in range(13)]
    + [
        "rms_db_mean",
        "rms_db_std",
        "rms_db_p10",
        "rms_db_p90",
        "zero_crossing_mean",
        "zero_crossing_std",
        "spectral_centroid_mean",
        "spectral_centroid_std",
    ]
)


class AccentQAError(RuntimeError):
    """Audio or model input could not be evaluated safely."""


def _canonical_identity(payload: object) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def feature_identity() -> str:
    return _canonical_identity(
        {
            "version": FEATURE_VERSION,
            "sample_rate": TARGET_SAMPLE_RATE,
            "max_analysis_seconds": MAX_ANALYSIS_SECONDS,
            "feature_names": FEATURE_NAMES,
        }
    )


def _read_bounded_audio(path: Path) -> np.ndarray:
    try:
        info = sf.info(str(path))
    except (OSError, RuntimeError) as exc:
        raise AccentQAError("accent QA audio is unreadable") from exc
    if info.frames <= 0 or info.samplerate <= 0:
        raise AccentQAError("accent QA audio contains no samples")
    if info.channels != 1:
        raise AccentQAError("accent QA requires mono audio")

    maximum_frames = max(1, round(MAX_ANALYSIS_SECONDS * info.samplerate))
    segments: list[np.ndarray] = []
    try:
        with sf.SoundFile(str(path), mode="r") as audio:
            if info.frames <= maximum_frames:
                samples = audio.read(info.frames, dtype="float32", always_2d=False)
                segments.append(np.asarray(samples, dtype=np.float32))
            else:
                window_frames = maximum_frames // 3
                offsets = (0, (info.frames - window_frames) // 2, info.frames - window_frames)
                for offset in offsets:
                    audio.seek(offset)
                    samples = audio.read(window_frames, dtype="float32", always_2d=False)
                    segments.append(np.asarray(samples, dtype=np.float32))
    except (OSError, RuntimeError) as exc:
        raise AccentQAError("accent QA audio decoding failed") from exc
    decoded = np.concatenate(segments)
    if not np.all(np.isfinite(decoded)) or float(np.max(np.abs(decoded))) < 1e-7:
        raise AccentQAError("accent QA audio is silent or contains invalid samples")
    if info.samplerate != TARGET_SAMPLE_RATE:
        divisor = math.gcd(info.samplerate, TARGET_SAMPLE_RATE)
        decoded = resample_poly(
            decoded,
            TARGET_SAMPLE_RATE // divisor,
            info.samplerate // divisor,
        ).astype(np.float32, copy=False)
    return decoded


def _mel_filterbank(*, sample_rate: int, fft_size: int, bands: int) -> np.ndarray:
    def hz_to_mel(value: float) -> float:
        return 2595.0 * math.log10(1.0 + value / 700.0)

    def mel_to_hz(value: np.ndarray) -> np.ndarray:
        return 700.0 * (np.power(10.0, value / 2595.0) - 1.0)

    points = mel_to_hz(np.linspace(hz_to_mel(40.0), hz_to_mel(sample_rate / 2), bands + 2))
    bins = np.floor((fft_size + 1) * points / sample_rate).astype(int)
    bins = np.clip(bins, 0, fft_size // 2)
    filters = np.zeros((bands, fft_size // 2 + 1), dtype=np.float32)
    for index in range(bands):
        left, center, right = bins[index : index + 3]
        if center > left:
            filters[index, left:center] = np.arange(center - left) / (center - left)
        if right > center:
            filters[index, center:right] = (right - np.arange(center, right)) / (right - center)
    return filters


def extract_accent_features(path: Path) -> dict[str, float]:
    """Extract deterministic MFCC/prosodic statistics with bounded memory."""

    samples = _read_bounded_audio(path)
    emphasized = np.empty_like(samples)
    emphasized[0] = samples[0]
    emphasized[1:] = samples[1:] - 0.97 * samples[:-1]
    frame_length = round(0.025 * TARGET_SAMPLE_RATE)
    frame_step = round(0.010 * TARGET_SAMPLE_RATE)
    if len(emphasized) < frame_length:
        emphasized = np.pad(emphasized, (0, frame_length - len(emphasized)))
    frame_count = 1 + (len(emphasized) - frame_length) // frame_step
    shape = (frame_count, frame_length)
    strides = (emphasized.strides[0] * frame_step, emphasized.strides[0])
    frames = np.lib.stride_tricks.as_strided(
        emphasized, shape=shape, strides=strides, writeable=False
    )
    windowed = np.asarray(frames * np.hamming(frame_length), dtype=np.float32)
    power = np.abs(np.fft.rfft(windowed, n=512, axis=1)).astype(np.float32) ** 2
    mel_energy = np.maximum(
        power @ _mel_filterbank(sample_rate=TARGET_SAMPLE_RATE, fft_size=512, bands=26).T, 1e-12
    )
    mfcc = dct(np.log(mel_energy), type=2, axis=1, norm="ortho")[:, :13]

    raw_samples = (
        samples
        if len(samples) >= frame_length
        else np.pad(samples, (0, frame_length - len(samples)))
    )
    raw_frames = np.lib.stride_tricks.as_strided(
        raw_samples,
        shape=shape,
        strides=strides,
        writeable=False,
    )
    rms = np.sqrt(np.mean(np.square(raw_frames, dtype=np.float64), axis=1) + 1e-12)
    rms_db = 20.0 * np.log10(rms)
    zcr = np.mean(np.diff(np.signbit(raw_frames), axis=1), axis=1)
    frequencies = np.fft.rfftfreq(512, 1.0 / TARGET_SAMPLE_RATE)
    spectral_total = np.maximum(np.sum(power, axis=1), 1e-12)
    centroid = np.sum(power * frequencies, axis=1) / spectral_total

    values = np.concatenate(
        [
            np.mean(mfcc, axis=0),
            np.std(mfcc, axis=0),
            np.array(
                [
                    np.mean(rms_db),
                    np.std(rms_db),
                    np.percentile(rms_db, 10),
                    np.percentile(rms_db, 90),
                    np.mean(zcr),
                    np.std(zcr),
                    np.mean(centroid),
                    np.std(centroid),
                ]
            ),
        ]
    )
    if len(values) != len(FEATURE_NAMES) or not np.all(np.isfinite(values)):
        raise AccentQAError("accent QA feature extraction returned invalid values")
    return {
        name: round(float(value), 8) for name, value in zip(FEATURE_NAMES, values, strict=False)
    }


@dataclass(frozen=True)
class AccentPrototypeModel:
    payload: dict[str, object]

    @property
    def identity(self) -> str:
        return _canonical_identity(self.payload)

    @classmethod
    def load(cls, path: Path) -> AccentPrototypeModel:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AccentQAError("accent QA model is unavailable") from exc
        if not isinstance(payload, dict) or payload.get("schema") != MODEL_SCHEMA:
            raise AccentQAError("accent QA model schema is invalid")
        if payload.get("feature_version") != FEATURE_VERSION:
            raise AccentQAError("accent QA model feature version is stale")
        if payload.get("feature_names") != list(FEATURE_NAMES):
            raise AccentQAError("accent QA model feature order is invalid")
        return cls(payload)

    def classify(self, features: dict[str, float]) -> tuple[AccentClassification, float, float]:
        vector = np.array([features[name] for name in FEATURE_NAMES], dtype=np.float64)
        try:
            mean = np.array(self.payload["normalization_mean"], dtype=np.float64)
            scale = np.array(self.payload["normalization_scale"], dtype=np.float64)
            pt_br = np.array(self.payload["pt_br_centroid"], dtype=np.float64)
            pt_pt = np.array(self.payload["pt_pt_centroid"], dtype=np.float64)
            temperature = float(str(self.payload.get("temperature", 1.0)))
            br_threshold = float(str(self.payload["pt_br_confident_max_pt_pt_score"]))
            pt_threshold = float(str(self.payload["likely_pt_pt_min_score"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise AccentQAError("accent QA model parameters are invalid") from exc
        if any(len(item) != len(vector) for item in (mean, scale, pt_br, pt_pt)):
            raise AccentQAError("accent QA model dimensions are invalid")
        if np.any(scale <= 0) or temperature <= 0 or not 0 <= br_threshold < pt_threshold <= 1:
            raise AccentQAError("accent QA model thresholds are invalid")
        normalized = (vector - mean) / scale
        br_distance = float(np.mean(np.square(normalized - pt_br)))
        pt_distance = float(np.mean(np.square(normalized - pt_pt)))
        delta = max(-60.0, min(60.0, (br_distance - pt_distance) / temperature))
        pt_pt_score = 1.0 / (1.0 + math.exp(-delta))
        pt_br_score = 1.0 - pt_pt_score
        if pt_pt_score >= pt_threshold:
            classification: AccentClassification = "likely_pt_pt"
        elif pt_pt_score <= br_threshold:
            classification = "pt_br_confident"
        else:
            classification = "uncertain"
        return classification, pt_br_score, pt_pt_score


class AccentDetector:
    """Lazy detector; model loading and feature work happen only when called."""

    def __init__(self, model_path: Path, mode: AccentMode = "shadow") -> None:
        self.model_path = model_path
        self.mode = mode
        self._model: AccentPrototypeModel | None = None

    def analyze(self, path: Path) -> dict[str, object]:
        started = time.monotonic()
        base: dict[str, object] = {
            "policy_version": POLICY_VERSION,
            "feature_version": FEATURE_VERSION,
            "feature_identity": feature_identity(),
            "mode": self.mode,
            "classification": "uncertain",
            "pt_br_score": None,
            "pt_pt_score": None,
            "confidence": None,
            "reason_code": "accent_uncertain",
            "detector_status": "disabled" if self.mode == "off" else "unavailable",
            "retry_required": False,
            "blocks_promotion": False,
        }
        if self.mode == "off":
            base["analysis_seconds"] = 0.0
            return base
        try:
            if self._model is None:
                self._model = AccentPrototypeModel.load(self.model_path)
            features = extract_accent_features(path)
            classification, pt_br_score, pt_pt_score = self._model.classify(features)
            base.update(
                {
                    "model_identity": self._model.identity,
                    "classification": classification,
                    "pt_br_score": round(pt_br_score, 8),
                    "pt_pt_score": round(pt_pt_score, 8),
                    "confidence": round(max(pt_br_score, pt_pt_score), 8),
                    "reason_code": (
                        "accent_likely_pt_pt"
                        if classification == "likely_pt_pt"
                        else "accent_pt_br_confident"
                        if classification == "pt_br_confident"
                        else "accent_uncertain"
                    ),
                    "detector_status": "available",
                    "features": features,
                    "retry_required": self.mode == "enforce" and classification == "likely_pt_pt",
                    "blocks_promotion": self.mode == "enforce" and classification == "likely_pt_pt",
                }
            )
        except AccentQAError as exc:
            base.update(
                {
                    "error": str(exc),
                    "reason_code": "accent_detector_unavailable",
                    "blocks_promotion": self.mode == "enforce",
                }
            )
        base["analysis_seconds"] = round(time.monotonic() - started, 6)
        return base


def aggregate_accent_diagnostics(
    items: list[dict[str, object]], mode: AccentMode
) -> dict[str, object]:
    classifications = [str(item.get("classification", "uncertain")) for item in items]
    unavailable = sum(item.get("detector_status") != "available" for item in items)
    likely_indexes = [
        index for index, value in enumerate(classifications) if value == "likely_pt_pt"
    ]
    uncertain_indexes = [
        index for index, value in enumerate(classifications) if value == "uncertain"
    ]
    classification: AccentClassification
    if likely_indexes:
        classification = "likely_pt_pt"
    elif uncertain_indexes or unavailable:
        classification = "uncertain"
    else:
        classification = "pt_br_confident"
    return {
        "policy_version": POLICY_VERSION,
        "feature_identity": feature_identity(),
        "mode": mode,
        "classification": classification,
        "chunk_count": len(items),
        "likely_pt_pt_chunk_indexes": likely_indexes,
        "uncertain_chunk_indexes": uncertain_indexes,
        "unavailable_chunk_count": unavailable,
        "reason_code": (
            "accent_likely_pt_pt"
            if likely_indexes
            else "accent_detector_unavailable"
            if unavailable
            else "accent_uncertain"
            if uncertain_indexes
            else "accent_pt_br_confident"
        ),
        "retry_required": mode == "enforce" and bool(likely_indexes),
        "blocks_promotion": mode == "enforce" and bool(likely_indexes or unavailable),
    }
