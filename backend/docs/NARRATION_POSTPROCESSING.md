# Narration stem post-processing

Production raw TTS and the assembled raw narration are immutable. The current production
master remains unchanged until human listening approves a successor. The opt-in A/B tool
creates two deterministic candidates:

- `narration_post_conservative_v1`: gentle dynamics, -17 LUFS, -1.5 dBTP;
- `narration_post_balanced_v1`: moderate dynamics, -16 LUFS, -1.5 dBTP.

Both use streaming FFmpeg compression followed by measured two-pass EBU loudness control.
No denoise, gate, de-esser, EQ, time stretch, pause padding or pronunciation processing is
applied. Output is 48 kHz mono PCM16. Publication is atomic and validates duration, clipping,
loudness, true peak, checksum and source immutability. Provenance includes input/output SHA,
profile and implementation versions, FFmpeg version, identity, processing time and before/after
metrics (LUFS, LRA, sample/true peak, RMS, crest, DC, silence and physical format).

This optional experiment is downstream of canonical assembly. Physical chunks and editorial
blocks remain native 24 kHz and receive no gain, crossfade or resampling; complete narration
assembly performs the one 48 kHz conversion. Neither this experiment nor gain-only production
mastering can establish or repair speaker identity.

Plan and run:

```bash
PYTHONPATH=backend backend/.venv/bin/python \
  backend/scripts/evaluate_narration_postprocessing.py \
  --input /absolute/path/to/working/narration_assembled.wav \
  --output /absolute/writable/path/post-processing-benchmarks

# Add --run only after reviewing the plan.
```

This produces A raw, B conservative and C balanced plus `manifest.json` and listening
instructions. No candidate is promoted automatically. These are narration stems, not final
program masters. Program Audio V1 now mixes the canonical narration stem with one controlled
instrumental music stem and then applies a separate transparent program loudness pass. Gameplay
audio and SFX remain excluded.

## Duration telemetry and production errors

`GET /workflow/calibration/speaking-rate` exposes a robust bounded window of accepted takes for
Ideas Hub without invoking TTS. Final narration diagnostics retain target, prediction, actual
duration and calibration identity when the script snapshot contains a duration contract.

Active production errors are now separate from recovered history. A valid final narration with
`audio_phase=quality_unvalidated` is reusable; the alignment phase owns its own blocked/failed
decision. Eager block/transcription reloads use `populate_existing`, preventing an earlier empty
SQLAlchemy relationship from hiding blocks created later in the same session.

Recurring pronunciation/ASR disagreements are available read-only at
`GET /workflow/pronunciation/candidates`. They are deduplicated across workflows and linked to
take IDs, but remain pending diagnostics. Only explicit Pronunciation Lab review can promote a
lexicon entry.
