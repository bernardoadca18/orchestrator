# Narration integrity and voice-quality operations

## Production contract

Every source, assembled and mastered PCM16 WAV is validated as both decoded audio and a physical
RIFF container. Publication is atomic, followed by a reopen. The durable contract records SHA-256,
bytes, sample rate, channels, frames, subtype and sample-derived duration. Final assembly compares
expected frames, decoded physical duration and the values read back from PostgreSQL before the
workflow can become `audio_concluido`.

The final and block endpoints reconcile the active file against durable metadata on every request.
They support full GET, HEAD and Starlette byte ranges (including suffix/tail requests), publish a
SHA ETag and require cache revalidation. An inconsistent current-take pointer or replaced/truncated
file returns conflict instead of partial or stale audio. The frontend includes the current SHA in
the URL and remounts the player when that identity changes.

Take ASR validation compares the actual synthesis text—not canonical editorial text—with the
generated source WAV before promotion. Its versioned report separates ASR-unavailable from a
content rejection, treats measured low-confidence ASR as inconclusive, and includes normalized
WER/CER, deletion/insertion/repetition ratios, coverage,
longest missing span, trailing loss and obvious reordering. A rejected take uses the existing
bounded quality-retry path and a controlled new seed. Transport retries remain semantically
identical and Qwen still performs no hidden synthesis retry.

## Non-destructive reference and artifact inspection

The analyzer never edits its input:

```bash
cd /home/bernardo/workspaces/neovids/orchestrator/backend
PYTHONPATH=. .venv/bin/python scripts/audio_diagnostics.py \
  /home/bernardo/workspaces/neovids/qwen3-tts-workshop/data/references/ref.wav \
  > /tmp/bernardo-reference-diagnostics.json
```

It reports physical integrity, clipping, peak, DC, a windowed noise-floor estimate,
leading/trailing and internal silence, candidate transients/discontinuities and diagnostic 50/60
Hz ratios. Candidate clicks require listening confirmation. The tool performs no gating,
denoising, de-click, normalization or overwrite. Any future cleaned derivative must use a new file,
recipe/version and checksum; the raw reference remains immutable.

## ICL versus speaker-embedding-only benchmark

`bernardo_v1` remains ICL. `bernardo_xvec_v1` is an explicit Qwen profile using
`x_vector_only_mode=True`, the same raw reference audio and no transcript. Clone mode is part of
the voice identity and Qwen prompt cache key.

After rebuilding/restarting Qwen so it loads the new profile, run the expensive benchmark only by
operator choice:

```bash
cd /home/bernardo/workspaces/neovids/orchestrator
docker compose exec api python scripts/benchmark_clone_modes.py \
  --output /data/audio/benchmarks/icl-vs-xvec-v1 \
  --voices bernardo_v1 bernardo_xvec_v1 \
  --generation narration_v1 \
  --asr-language pt \
  --seed 12345
```

The seven fixed categories use identical synthesis text, normalizer/pronunciation identities,
the synthesis language (`Portuguese`), generation profile, seed, model and revision for both
voices. Faster-Whisper uses the independent ISO ASR language (`pt` by default); pass an empty value
to enable autodetection. JSONL records physical and Qwen integrity, duration/RTF, ASR coverage,
pause/silence and transient candidates. Fill the human review fields for timbre, naturality, pace,
accent, pauses and pronunciation. The script never
declares a winner or changes the production profile. Each invocation creates a fresh idempotency
scope and output subdirectory by default; pass `--run-id` only to deliberately resume the same
benchmark run.

A raw-versus-cleaned-reference A/B is intentionally not enabled until an isolated derivative and
reviewed surgical cleanup recipe exist. When it does, define separate voice profile names for all
four cells (ICL raw/clean and x-vector raw/clean); never redirect `bernardo_v1`.

## Professional prosody and timbre transfer

`app.services.prosody_transfer_experiment` is an opt-in provider-neutral boundary, not production
dispatch. A `GuideTTSProvider` owns wording, timing, rhythm, intonation and style. A
`TimbreTransferProvider` receives guide audio plus an immutable target reference. The runner
validates both artifacts, requires guide-timing preservation, records identities and diagnostics,
and always marks the manifest `production_promoted=false`.

Providers expose `release()`, and the runner releases guide TTS before loading voice conversion,
which supports the 12 GB single-GPU workstation. Candidate Qwen CustomVoice/VoiceDesign guide
profiles and Seed-VC/OpenVoice/newer VC implementations still require separate license,
maintenance, PT-BR, leakage, similarity, timing and artifact benchmarks. No checkpoint is loaded or
downloaded by unit tests, and the deprecated Kokoro/Seed-VC production path is not restored.

## Deploy and rollback

Before starting backend code that maps revision 013:

```bash
cd /home/bernardo/workspaces/neovids/orchestrator
docker compose run --rm api alembic upgrade head
docker compose up -d --build api worker frontend
```

Qwen needs a rebuild/restart to expose `bernardo_xvec_v1`; production remains `bernardo_v1`.

Rollback application containers first, then (only if no new deployment is using revision 013):

```bash
cd /home/bernardo/workspaces/neovids/orchestrator
docker compose run --rm api alembic downgrade 012
```

The downgrade removes only nullable diagnostics columns. Historical take rows and WAVs are not
fabricated, overwritten or deleted. Do not reset PostgreSQL volumes or voice references.
