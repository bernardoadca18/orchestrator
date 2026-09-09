# Narration integrity and voice-quality operations

## Production contract

Every source, assembled and mastered PCM16 WAV is validated as both decoded audio and a physical
RIFF container. Publication is atomic, followed by a reopen. The durable contract records SHA-256,
bytes, sample rate, channels, frames, subtype and sample-derived duration. Final assembly compares
expected frames, decoded physical duration and the values read back from PostgreSQL before the
workflow can become `audio_concluido`.

Editorial blocks remain at Qwen's native 24 kHz. Their sole source is the ordered active
`AudioChunkTake` selection. `orchestrator_native_pcm16_block_v1` hashes a canonical manifest of
block/order, chunk/take IDs, source SHA/frame/rate/format and frozen pause frames. The assembler
copies decoded PCM16 without gain or resampling, verifies every chunk output range by PCM hash and
every pause as exact zeros, then validates and atomically publishes the block. Only complete final
narration assembly resamples once to 48 kHz. A remote full-job WAV is retained only as forensic
provenance and is never published as the block after physical chunks exist.

The final and block endpoints reconcile the active file against durable metadata on every request.
They support full GET, HEAD and Starlette byte ranges (including suffix/tail requests), publish a
SHA ETag and require cache revalidation for mutable block/final URLs. UUID-addressed physical takes
and candidates use immutable caching. An inconsistent current-take pointer, stale assembly manifest
or replaced/truncated file returns conflict instead of partial or stale audio. The frontend
includes the current SHA in the URL and remounts the player when that identity changes.

## Speaker identity diagnostics

Content ASR and speaker identity are independent. Qwen reuses its already loaded speaker encoder
to attach `speaker_similarity`, reference identity, policy version and
`passed | warning | unavailable` to every physical chunk/candidate; the Orchestrator persists these
values and block summaries. Calibration on incident data found known-good chunks at approximately
`0.988–0.994`, while two suspect chunks scored `0.968901` and `0.980853`. A suspect full block still
scored `0.994105`, so whole-block similarity is not a safe acceptance gate. Policy
`qwen_ecapa_warning_v1` uses `0.985` as a chunk-level warning only. Automatic blocking is deferred
until a broader labeled corpus has clean separation; warnings require operator listening.

Lightweight performance metadata separates remote-job wait, full/chunk download, chunk
persistence, block write/validation/atomic publication, final assembly and mastering. These
monotonic durations live in existing take/workflow diagnostic JSON; no monitoring service or
background worker was added.

Take ASR validation compares the actual synthesis text—not canonical editorial text—with the
generated source WAV before promotion. Comparison-only normalization expands equivalent numeric
representations and resolves already approved pronunciation aliases on the ASR hypothesis; it does
not alter canonical, normalized, or synthesis text. The `narration_content_coverage_v2` report
separates content integrity from pronunciation accuracy. WER and CER remain diagnostics, while a
blocking decision requires structural evidence: genuine deletion runs, a structurally missing
sentence, terminal loss, abnormal repetition, or strong reordering. Substitutions are not counted
as missing spans.

The classifications and actions are:

- `content_complete`: accept;
- `pronunciation_or_asr_disagreement`: accept with warning, record local mismatches, no retry;
- `content_inconclusive`: preserve `quality_unvalidated` semantics, no blind semantic retry;
- `probable_content_loss`, `premature_end`, `probable_repetition`, or
  `probable_reordering`: reject and use the bounded quality-retry path;
- physical/container and Qwen duration/runaway guards remain independent hard failures.

Long-form takes use one timestamped ASR pass. Qwen's immutable `assembly_timeline` partitions those
words into chunk windows for per-chunk diagnostics; the aggregate structural report remains the
decision authority. This localizes a warning or missing span without loading/transcribing the WAV
again. Every decision records classification, retry requirement/reason, region and sentence
coverage, deletion/substitution metrics, local mismatch candidates, and compact chunk reports.
Measured low-confidence or unavailable ASR stays inconclusive. Transport retries remain
semantically identical; only blocking quality classes get a new controlled seed, and Qwen still
performs no hidden synthesis retry.

In Workflow Detail, open `Histórico` and then `Qualidade de conteúdo / ASR` for the decision,
metrics, per-chunk evidence, local pronunciation mismatches, and raw ASR transcript. A failed take
should be diagnosed from `classification`, `reasons`, longest missing span, sentence/region
coverage, and retry reason—not from WER alone.

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

`bernardo_v1` remains the historical ICL profile. `bernardo_xvec_v1` is an explicit historical Qwen profile using
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

The current production freeze uses the separately versioned `bernardo_xvec_v2` and
`narration_v2`; see `PRODUCTION_VOICE_BASELINE.md`. The historical profiles above remain
registered and this document's comparison commands stay reproducible.

Rollback application containers first, then (only if no new deployment is using revision 013):

```bash
cd /home/bernardo/workspaces/neovids/orchestrator
docker compose run --rm api alembic downgrade 012
```

The downgrade removes only nullable diagnostics columns. Historical take rows and WAVs are not
fabricated, overwritten or deleted. Do not reset PostgreSQL volumes or voice references.
