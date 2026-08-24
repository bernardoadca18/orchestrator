# NeoVids Orchestrator

The Orchestrator consumes an approved Ideas Hub production script and produces one traceable,
production-ready narration. The canonical voice dependency is `qwen3-tts-workshop`; the former
Kokoro/Seed-VC Voice Workshop is deprecated reference code and is not an operational dependency.

## Stage 12.5 audio pipeline

```text
canonical script block
  -> deterministic pronunciation preprocessing
  -> synthesis text
  -> Qwen Voice Service async job
  -> immutable native source WAV per editorial block
  -> deterministic block assembly (pause, 48 kHz mono PCM16)
  -> transparent whole-narration gain mastering
  -> final WAV identity
  -> Faster-Whisper word timestamps
```

The Orchestrator's `AudioBloco` is an editorial block. Qwen inference chunks remain private to the
Voice Service. Independent speech blocks are never crossfaded. Qwen-native WAVs under
`data/audio/<workflow>/blocks/` are preserved; resampling occurs once at final assembly.

The final mastering policy measures Integrated LUFS, sample peak and real True Peak (FFmpeg EBU
R128), applies constant gain toward -16 LUFS constrained by -1 dBTP, reopens/reanalyses a temporary
PCM16 WAV, verifies duration, and atomically publishes `narration_final.wav`. It deliberately adds
no compressor, limiter, EQ or denoising.

## Voice contract and recovery

The provider-neutral client uses the actual Voice Service contract:

- `GET /v1/voices` and `GET /v1/generation-profiles` resolve immutable profile identities;
- `POST /v1/narrations` creates an async job with `Idempotency-Key`;
- `GET /v1/narrations/{id}` polls `queued | running | completed | failed`;
- `GET /v1/narrations/{id}/audio` downloads the completed WAV.

The stable creation key includes workflow, block order and synthesis identity. A lost POST response
is retried with the identical request/key. Remote job ID and resolved seed are committed before
polling. Worker startup reconciles orphaned jobs. A failed remote take receives a persisted retry
attempt key but retains the same resolved seed; a changed text/glossary/profile identity starts a
new take with the configured seed policy.

A completed block is reused only when its synthesis identity is current and the local WAV reopens
with the persisted checksum, size, rate and channels. Thus a failure in block 8 does not replace
valid takes 1–7. Missing local output is redownloaded from the same remote job when available.

## Canonical text, synthesis text, and time

- **Script text** is the immutable editorial `conteudo` from Ideas Hub.
- **Synthesis text** is a derived pronunciation variant sent to Qwen. Configure global/project
  entries in `backend/config/pronunciation_glossary.yaml`.
- **Gameplay time** belongs to a `VisualQuery` preferred source interval.
- **Narration time** in this stage is only the exact assembled start/end of each audio block.

Stage 13 will align canonical narration content and VisualQuery excerpts. No fuzzy/forced narration
alignment is implemented here.

## Final identity and transcription

Workflow state exposes duration, SHA-256, byte size, 48 kHz sample rate, mono channels, mastering
configuration and measured before/after metrics. Host paths are not returned by the public schemas.
Transcription is upserted by final audio SHA-256. `FasterWhisperNarrationProvider` lazily creates and
caches one model per worker process (CPU/int8 by default); a transcription failure does not corrupt
or invalidate a safely completed final WAV.

## Configuration

Copy `.env.example`. Important settings include:

- `VOICE_API_URL`, `VOICE_PROFILE`, `VOICE_GENERATION_PROFILE`, `VOICE_LANGUAGE`, `VOICE_SEED`;
- separate create/status/download/overall timeouts and bounded HTTP retry/backoff;
- `SILENCIO_ENTRE_BLOCOS_MS`, `AUDIO_FINAL_SAMPLE_RATE`;
- LUFS/True Peak policy and duration tolerance;
- `TRANSCRIPTION_MODEL`, `TRANSCRIPTION_DEVICE`, `TRANSCRIPTION_COMPUTE_TYPE`.

No raw Qwen sampling controls are part of this service's production configuration.

## Local operation

Start `qwen3-tts-workshop` separately; it publishes host port 8889 for the
container-to-host integration and must remain on a trusted/private host. Then:

```bash
cp .env.example .env
./setup_orchestrator.sh
docker compose up --build
```

Containers reach the separately deployed Voice Service through
`VOICE_API_URL_DOCKER` (default `http://host.docker.internal:8889`). API: port 8011; frontend: 5174.
Persistent narration files live under `data/audio`; the worker's Hugging Face/Whisper cache lives
under `cache/huggingface`; PostgreSQL and Redis use Compose volumes.
The separately deployed Ideas Hub is resolved through `IDEAS_HUB_API_URL_DOCKER` (default host port
8000).

Useful checks:

```bash
cd backend
.venv/bin/pytest
.venv/bin/ruff check app tests
.venv/bin/ruff format --check app tests
.venv/bin/mypy app
.venv/bin/alembic upgrade head
cd ..
docker compose config
```

`GET /health` is liveness. `GET /health/dependencias` verifies PostgreSQL, Redis, Ideas Hub and the
Voice Service's `/health/ready` endpoint.

## Production handoff

`GET /workflow/{id}/pacote-producao` returns script blocks, structured `visual_queries`, logical
audio URLs, block/final objective metadata, mastering provenance and hash-bound transcription.
Older scripts without `visual_queries` remain valid and return an empty list. See
`backend/docs/HANDOFF_VISUAL_PRODUCTION.md`.

## Limits

This stage is single-workflow-worker oriented. The Voice Service independently owns its bounded
single-GPU queue. Final video rendering, Media Search, Visual Director, Production Timeline and
Stage 13 Narration Timeline/Voice Alignment are intentionally absent.
