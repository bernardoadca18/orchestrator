# Long-form resource hardening

The production audio path is designed around bounded working sets. It does not retain an entire
narration as NumPy arrays:

- final assembly reads and writes fixed-size blocks, preserves deterministic pauses/offsets and
  atomically replaces the destination only after physical validation;
- WAV metadata/hash inspection does not decode the signal; peak/clipping inspection uses bounded
  blocks;
- narration diagnostics use two bounded passes. Hum analysis intentionally retains at most the
  first 120 seconds, so its peak is constant rather than proportional to narration duration;
- mastering v2 measures LUFS/True Peak through FFmpeg EBU R128, applies the single transparent gain
  in fixed-size blocks, and publishes atomically. Its production thresholds are unchanged.

`transparent_gain_v2_streaming_ebur128` is an explicit provenance version because FFmpeg EBU R128
replaces the previous whole-file `pyloudnorm` measurement. Regression tests require agreement within
0.05 LU on representative signals and exact frame/sample-rate preservation. This is measurement
parity, not a new loudness policy.

The CPU-only memory harness is strict plan-only unless `--run` is supplied:

```bash
cd backend
PYTHONPATH=. .venv/bin/python scripts/benchmark_audio_memory.py \
  --operation all --minutes 10 60

PYTHONPATH=. .venv/bin/python scripts/benchmark_audio_memory.py \
  --operation all --minutes 10 60 --run \
  --output /tmp/neovids-audio-memory.json
```

Each case runs in an isolated child process and reports `ru_maxrss`; no CUDA, provider, database or
queue is used. A flat 10/60-minute curve is the acceptance signal, not a particular machine-specific
RSS number.

Faster-Whisper inference is serialized. `TRANSCRIPTION_MODEL_LIFETIME=resident` is the compatible
default and reuses one lazy model. `per_job` creates a model per transcription and drops the service
reference after all segments have been consumed. Diagnostics expose policy/load/release counters,
but a dropped Python reference is not proof that an allocator returned RAM or VRAM; that must be
measured with a separate real-model, opt-in smoke before choosing `per_job` operationally.

Rollback is configuration/code based: restore `resident` for ASR or deploy the preceding service
version. Existing WAVs, database rows, profiles, thresholds, seeds, chunking and generation settings
are unaffected; no migration is involved.
