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

## Stage 13 Narration Timeline / Voice Alignment

- **Script text** is the immutable editorial `conteudo` from Ideas Hub.
- **Synthesis text** is a derived pronunciation variant sent to Qwen. Configure global/project
  entries in `backend/config/pronunciation_glossary.yaml`.
- **Gameplay time** belongs to a `VisualQuery` preferred source interval.
- **Narration time** is a timestamp inside the final mastered narration WAV.

Stage 13 first partitions Faster-Whisper words using the exact assembled block offsets. Within each
block it performs deterministic sequence alignment from canonical tokens to pronunciation-adjusted
synthesis tokens and then to ASR words. `VisualQuery.narration_excerpt` is searched only in its own
canonical block: literal, normalized, accent-insensitive, then a bounded token-fuzzy fallback.
Repeated or weak excerpts become `ambiguous` or `unresolved`; timestamps are never estimated from
characters, reading speed or gameplay time.

Persisted `NarrationAlignment`, compact per-block metrics and
`VisualQueryNarrationBinding` records are versioned by `narration_alignment_v1`. The compatibility
fingerprint includes final audio SHA, relevant script/excerpts, block synthesis identities and
offsets, full transcription identity and strategy version. The same inputs reuse one run.

Endpoints:

- `GET /workflow/{id}/narration-timeline` returns the current compatible read model;
- `POST /workflow/{id}/narration-alignment` builds/reuses it synchronously;
- `POST ...?force=true` rebuilds only derived rows—never Qwen, mastering or ASR.

Alignment runs automatically after a current transcription is available. Missing/stale
transcription yields `blocked`; algorithm/input failure yields `failed`. Neither state invalidates a
healthy `audio_concluido` workflow.

## Stage 14 Visual Director

Stage 14 is a synchronous, independently rebuildable production-domain service inside the
Orchestrator. It consumes immutable `VisualQuery` records, the compatible completed Stage 13
alignment and Media Indexer's public HTTP API. It selects logical gameplay source windows and does
not download media, run FFmpeg, trigger indexing or create final edit placement.

Selection is deterministic and versioned as `visual_director_v1`:

1. validate trusted `preferred_session_id` and gameplay range through `GET /sessions/{id}`;
2. select valid trusted provenance directly, with zero Media Search calls;
3. otherwise call `POST /media-search/sessions/{id}` or
   `POST /media-search/projects/{id}` using the explicit script media scope;
4. normalize and deduplicate overlapping candidates;
5. apply semantic relevance, weak preferred-session/source-note/duration bonuses and a modest
   overlap-reuse penalty;
6. persist the selected source and at most two compact alternatives.

The default semantic threshold is the Media Search contract's cosine-like `0.25`. A weak result is
left `unresolved`; provider outage becomes `failed`, never "no results". Required and optional gaps
remain explicit in `VisualPlan.status=completed_with_gaps` without invalidating script, audio or
narration alignment.

Endpoints:

- `GET /workflow/{id}/visual-plan` reads the current compatible artifact without hidden search;
- `POST /workflow/{id}/visual-plan` creates/reuses it;
- `POST ...?force=true` re-runs only source resolution and may call Media Search again.

Searches use a request-scoped duplicate-query cache and bounded concurrency. The persisted
fingerprint contains script/query identity, Stage 13 alignment, media scope, policy version and the
resolved TemporalIndex/search provenance. Since Media Indexer has no cheap "latest index identity"
read contract, discovering a newly-created index requires an explicit force rebuild.

## Stage 15 Production Timeline

Stage 15 is a synchronous, local metadata planner inside the Orchestrator. It combines the exact
final-narration identity, the current completed NarrationAlignment and the current completed or
gapped VisualPlan into a renderer-neutral `ProductionTimeline`. It performs no Media Search, voice,
ASR, FFmpeg, file materialization or rendering.

`production_timeline_v1` uses final narration time as final video time. Resolved VisualDecisions are
placed on a single non-overlapping primary visual track. Exact-fit windows are retained; longer
Media Search windows use an available semantic-cluster anchor or deterministic center trim; trusted
preferred provenance uses a documented start-biased trim. Short windows can consume at most one
Stage 14 alternative. Playback rate is always `1.0`, transitions are always `cut`, and no hidden
loop, freeze-frame or speed manipulation exists.

Conflicts use `required > preferred > optional`, then script order. Lower-priority requirements
remain explicitly partial/unplaced. Uncovered intervals may extend adjacent footage only within the
known Stage 14 candidate window; every remaining interval is a typed gap. Continuous adjacent
placements are coalesced only for the same source identity and continuous source time, preserving
all contributing VisualQuery and VisualDecision IDs.

Endpoints:

- `GET /workflow/{id}/production-timeline` is a side-effect-free compatible read;
- `POST /workflow/{id}/production-timeline` creates/reuses the timeline;
- `POST ...?force=true` recomputes only timeline metadata from frozen upstream artifacts.

The independent validator checks narration identity, range containment, timeline ordering, absence
of primary-track overlap, playback-rate/source-duration identity, explicit gap complement and exact
coverage metrics before a timeline can complete. Calculations use integer microseconds internally;
the API exposes seconds with `[start,end)` semantics.

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
- alignment strategy, fuzzy/ambiguity thresholds, minimum speech coverage and complexity guards.
- `MEDIA_INDEXER_API_URL`, timeout/retry settings and the bounded Visual Director search policy.
- Production Timeline strategy/schema versions, 0.5 s minimum visual duration, bounded source
  fallback and deterministic gap-fill/coalescing policy.

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
The separately deployed Media Indexer is resolved through `MEDIA_INDEXER_API_URL_DOCKER` (default
host port 8012).

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
audio URLs, block/final objective metadata, mastering provenance, hash-bound transcription and a
separate compatible `narration_alignment` section. When available, `visual_plan` is another separate
derived section containing gameplay source windows and alternatives; the original VisualQueries are
not rewritten. `production_timeline` remains a separate derived section containing the narration
track, exact gameplay trims, primary-track placements, explicit gaps and renderer-facing metrics.
Older scripts without `visual_queries` remain valid and return an empty list. See
`backend/docs/HANDOFF_VISUAL_PRODUCTION.md`.

## Limits

This stage is single-workflow-worker oriented. The Voice Service independently owns its bounded
single-GPU queue. Alignment uses deterministic text/ASR evidence, not a forced-alignment model; very
large blocks are rejected by configured token/cell guards. The Visual Director supports gameplay
sources only and depends on existing searchable Media Indexer indexes. The Production Timeline
supports one narration track and one primary gameplay track; FPS/render profile, source
materialization, composition, Remotion and final rendering remain intentionally deferred to Stage
16. The current local Media Indexer snapshot still has no completed TemporalIndex among its 104
failed sessions, so real gameplay-selection and resulting edit-quality inspection remain explicit
external validation debt before the complete visual production pipeline can be frozen.
