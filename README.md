# NeoVids Orchestrator

The opt-in x-vector-only voice reference regression workflow is documented in
[backend/docs/REFERENCE_REGRESSION.md](backend/docs/REFERENCE_REGRESSION.md).

The Orchestrator consumes an approved Ideas Hub production script and produces one traceable,
production-ready narration. The canonical voice dependency is `qwen3-tts-workshop`; the former
Kokoro/Seed-VC Voice Workshop is deprecated reference code and is not an operational dependency.

## Stage 12.5 audio pipeline

```text
canonical script block
  -> deterministic Portuguese text normalization
  -> deterministic pronunciation glossary
  -> synthesis text
  -> Qwen Voice Service async job
  -> immutable native source WAV + take-level ASR content gate
  -> deterministic block assembly (pause, 48 kHz mono PCM16)
  -> transparent whole-narration gain mastering
  -> final WAV identity
  -> Faster-Whisper word timestamps
  -> versioned narration speech-quality gate
```

The Orchestrator's `AudioBloco` is an editorial block. Qwen still owns physical chunking and
synthesis, but completed physical chunks are downloaded, validated and persisted as durable
`AudioChunk` / initial `AudioChunkTake` children under
`data/audio/<workflow>/blocks/chunks/`. Their frozen plan records character span, boundary,
resolved `pause_after_frames`, chunker/profile identities, exact synthesis text and original seed
so the assembler reproduces that historical Qwen generation without consulting current pause
defaults. Independent speech blocks are never crossfaded;
resampling still occurs once at final assembly.

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
- `GET /v1/narrations/{id}/chunks` exposes immutable original physical artifacts;
- `POST /v1/narrations/{id}/chunks/{index}/regenerate` creates one idempotent UUID candidate;
- `GET /v1/narrations/{id}/chunk-candidates/{candidate_id}/audio` retrieves that exact candidate.

The stable creation key includes workflow, block order and synthesis identity. A lost POST response
is retried with the identical request/key. Remote job ID and resolved seed are committed before
polling. Worker startup reconciles orphaned jobs. Transport failures retain the same semantic
request and idempotency key. A `chunk_quality_guard` failure creates a bounded explicit quality
attempt with a new seed derived by `qwen-orchestrator-quality-retry-v1`; attempt, rejected job,
seed, category and retry strategy remain durable. Permanent profile/contract failures are not
retried. A changed text/normalizer/glossary/profile identity starts a new take under the configured
seed policy.

A completed block is reused only when its synthesis identity is current and the local WAV reopens
with the persisted checksum, size, rate, channels, sample count and subtype. Serving performs the
same reconciliation and uses SHA-addressed cache revalidation plus byte ranges. Thus a failure in block 8 does not replace
valid takes 1–7. Missing local output is redownloaded from the same remote job when available.

Chunk regeneration requires an idempotency key and derives one explicit manual seed from the
stable operation identity. The Orchestrator stores the exact returned candidate WAV under its own
take UUID and records the Qwen candidate UUID. Candidate completion does not move
`AudioChunk.current_take_id` and does not invalidate any downstream artifact. Preview addresses
the durable Orchestrator take UUID; rejection changes only that historical take. Activity is
derived from `current_take_id`, not a second persisted `active` status.

Promotion changes only the selected chunk take and increments durable block/workflow edit
revisions. The last successfully published narration remains current until
`POST /workflow/{workflow_id}/audio-edits/apply` queues one idempotent batch update. Its worker
freezes selected take IDs, rebuilds only dirty blocks, reuses clean block artifacts, and streams the
complete narration through the established assembler and mastering path. Database pointers move
only after the replacement validates. Pre-publication failure or a newer concurrent promotion
leaves the old narration current and the edits pending. After publication, SHA-based transcription
compatibility and the existing Stage 17 identity chain recover only timing-dependent artifacts.

In `WorkflowDetail`, creators expand an audio block to inspect and preview its physical chunks,
regenerate one chunk, then either select the candidate with **Usar este take** or preserve it as
rejected history with **Descartar**. Selections accumulate as pending narration edits; the single
workflow-level **Atualizar Pipeline** action starts the durable batch operation and polls its
`pending | running | completed | failed` state. Published block/final players remain on their
previous SHA until that operation completes, while downstream production blockers remain visible
as an independent state.

Alembic lineage is linear: revision `014` adds production-error history, revision `015` adds the
chunk/take tables, and revision `016` adds exact pause provenance plus durable edit revisions and
batch update operations.

The take-level content gate compares Faster-Whisper against the actual synthesis text and persists
WER/CER, deletions, insertions, repetitions, missing spans and premature-ending evidence before a
take becomes current. See `backend/docs/NARRATION_HARDENING.md` for integrity, diagnostics,
x-vector benchmark and experimental prosody-transfer operation.

## Pronunciation resolution and laboratory

The production glossary is a versioned YAML lexicon of enabled, human-approved entries rather than
an unstructured replacement map. Its one-pass Unicode/token-aware resolver supports explicit
categories, locale/project scope, case policy, priority, stable entry revisions, raw-spelling
no-ops and per-occurrence span provenance. Ambiguous or non-idempotent configurations fail at
load. Canonical script text remains immutable.

The operator CLI validates and previews without synthesis, runs bounded controlled Qwen candidate
matrices, keeps WAV/JSONL evidence including quality-guard failures, records human reviews and
performs optimistic atomic promotion/disable mutations. A candidate is never promoted from a
machine score or lab result alone. Examples:

```bash
cd backend
.venv/bin/python scripts/pronunciation.py validate
.venv/bin/python scripts/pronunciation.py resolve --text 'A Rockstar Games mostrou GTA 6.'
.venv/bin/python scripts/pronunciation.py lab-run \
  --target GTA --context 'A análise de {target} começa agora.' \
  --initialism-candidates --generation narration_temp080 --seeds 101
```

Phrase-aware title numerals, diagnostic candidates and the opt-in pacing A/B contract are described
in [`docs/PRONUNCIATION_AND_PACING_V2.md`](docs/PRONUNCIATION_AND_PACING_V2.md).

See `backend/docs/PRONUNCIATION_LAB.md` for the lexicon schema, approval workflow, all CLI
commands, controlled real-Qwen progression and ownership constraints. Pronunciation rewriting is
never performed inside `qwen3-tts-workshop`.

## Stage 13 Narration Timeline / Voice Alignment

- **Script text** is the immutable editorial `conteudo` from Ideas Hub.
- **Synthesis text** is a deterministic derivative sent to Qwen: conservative Portuguese text
  normalization first, then global/project pronunciation entries from
  `backend/config/pronunciation_glossary.yaml`. Normalizer and glossary identities participate in
  the block synthesis identity; the canonical editorial text is never overwritten.
- **Gameplay time** belongs to a `VisualQuery` preferred source interval.
- **Narration time** is a timestamp inside the final mastered narration WAV.

Stage 13 first partitions Faster-Whisper words using the exact assembled block offsets. Within each
block it performs deterministic sequence alignment from canonical tokens to pronunciation-adjusted
synthesis tokens and then to ASR words. Long blocks use unique monotonic token anchors and bounded
exact windows; an unanchored exceptional region is reported as inconclusive rather than allocating
an unbounded matrix. `VisualQuery.narration_excerpt` is searched only in its own canonical block:
literal, normalized, accent-insensitive, then a bounded token-fuzzy fallback.
Repeated or weak excerpts become `ambiguous` or `unresolved`; timestamps are never estimated from
characters, reading speed or gameplay time.

Persisted `NarrationAlignment`, compact per-block metrics and
`VisualQueryNarrationBinding` records are versioned by `narration_alignment_v3`. Host-owned visual
beat offsets bind directly to their full literal narration spans; historical queries without those
offsets retain the v2 excerpt-matching compatibility path. The compatibility
fingerprint includes final audio SHA, relevant script/excerpts, block synthesis identities and
offsets, full transcription identity and strategy version. The same inputs reuse one run.

Endpoints:

- `GET /workflow/{id}/narration-timeline` returns the current compatible read model;
- `POST /workflow/{id}/narration-alignment` builds/reuses it synchronously;
- `POST ...?force=true` rebuilds only derived rows—never Qwen, mastering or ASR.

Alignment runs automatically after a current transcription is available. Missing/stale
transcription yields `blocked`; algorithm/input failure yields `failed`. The versioned
`narration_speech_quality_v2` policy evaluates content as ordered block segments and aggregates
global deletion spans, ending loss, repetition and reordering. Each persisted block diagnostic
includes token counts, classification and bounded-window usage, so a bad block cannot disappear
inside a healthy average. Catastrophic evidence blocks Stage 17 while preserving source WAVs, the
final WAV, transcription and alignment diagnostics. Harmless pronunciation differences do not
require perfect WER. Neither state deletes or invalidates the technically valid `audio_concluido`
artifact; `audio_phase=quality_unvalidated` distinguishes it from production-validated narration
when transcription/alignment evidence is unavailable.

## Stage 14 Visual Director

Workflow creation performs a read-only Ideas Hub visual-readiness preflight before persisting or
enqueuing any audio work. A final-production script that lacks contiguous host-owned Visual Beats
returns HTTP 409 with `production_script_not_visual_ready` plus the canonical readiness report;
therefore Qwen is never called for that request. Editorial approval and visual-production readiness
remain separate contracts. The Stage 14 zero-query gate remains as defense for historical workflow
snapshots created before this preflight.

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

For the known final visual-production profiles, non-empty narration with zero structured
`VisualQuery` requirements is not a successful empty plan. Stage 14 persists a blocked diagnostic
with category `visual_queries_missing`, and Stage 17 records that same stable category in workflow
error history. Historical scripts without queries remain readable and available in production
packages; they need a new visual-enriched Ideas Hub revision before final visual production.

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

## Stage 17 End-to-End Production Orchestration

New workflows now enter a durable end-to-end state machine:

```text
audio -> narration_alignment -> visual_plan -> production_timeline -> render -> completed
```

`WorkflowExecucao.status` remains the historical audio lifecycle (`agendada`, `gerando_audio`,
`audio_concluido`, `erro`, `cancelada`). `production_status` is the canonical Stage 17 lifecycle
(`queued`, `running`, `blocked`, `failed`, `completed`, `cancelled`) and `production_phase` records
the current boundary. Legacy rows keep both production fields `NULL` and do nothing until
`POST /workflow/{id}/resume-production` explicitly adopts them.

The single Celery worker advances frozen Stage 13–15 services with `force=False`. It reuses valid
audio and compatible derived artifacts. A final timeline with any gap becomes `blocked` with a
stable diagnostic; Stage 17 never falls back to preview or invents footage. `blocked` means valid
operator input/artifacts are missing, while `failed` means the attempted operation failed.

For a gap-free current timeline, the Orchestrator persists a `WorkflowRender` and deterministic
`Idempotency-Key` before calling the separately deployed Video Composer. The request fingerprint
binds workflow, timeline ID/fingerprint, render profile and `mode=final`. A lost POST response or a
remote 404 is reconciled with the same key. Failed-render retry first GETs the known remote job and
only submits again without the original key when that job is still failed.

Composer polling uses one GET per Celery task. Each non-terminal result schedules a future task;
workers never sleep or long-poll. Startup requeues only `queued`/`running` productions and routes a
known render directly back to polling. Exhausted HTTP retries, including queue pressure (`429`),
remain `running` and are rescheduled until the persisted render timeout; healthy upstream artifacts
are not invalidated by a temporary Composer outage. Composer identity mismatch, timeout and stale
current timeline protection prevent the wrong MP4 from completing a workflow.

Endpoints:

- `GET /workflow/{id}/render` returns the current semantic final render without side effects;
- `GET /workflow/{id}/video` streams the Composer MP4 and forwards byte ranges;
- `POST /workflow/{id}/resume-production` resumes blocked or explicitly adopts legacy work;
- `PATCH /workflow/{id}/retry` selectively retries the failed phase;
- `PATCH /workflow/{id}/cancelar` cancels local scheduling (remote Composer cancellation is not
  available in the frozen Stage 16 contract).

Final audio remains available whenever its persisted artifact identity exists, regardless of a
downstream block, failure or cancellation.

## Stage 18 Automated Production QA

Stage 18 is a deterministic release gate over the frozen production chain. It persists a
fingerprinted `ProductionQAReport`; it never regenerates voice, runs Whisper or Media Search,
rebuilds alignment/planning/timeline, creates a render, remasters audio or repairs an upstream
artifact. `production_status=completed` means an MP4 exists. `publication_ready=true` means the
current compatible **standard** QA report passed (warnings are release-safe in v1). These states are
deliberately independent.

The automatic `standard` mode runs after Stage 17 completes and on worker recovery when a completed
workflow has no compatible report or left one `pending`/`running`. It validates artifact identities, final narration format and
persisted mastering measurements, transcription/alignment/visual-query requirements, a gap-free
full-coverage timeline, current render/profile/duration metadata and Composer delivery. Delivery is
bounded to `Range: bytes=0-0`; standard QA never downloads the whole MP4. Temporary Composer
outages yield `blocked`, use bounded Celery retries and never demote the production.

The manual `deep` mode adds a streamed full-content SHA-256 and independent `ffprobe`. It writes to
a generated temporary MP4, never buffers the full video in RAM, enforces
`PRODUCTION_QA_DEEP_MAX_VIDEO_BYTES`, and removes the file in `finally`. Deep reports have a
separate fingerprint and do not replace the standard release gate.

Policies are versioned. `game_analysis` resolves to `game_analysis_qa_v1`, requiring the complete
transcription/alignment/visual chain and all required VisualQueries. Null, unknown and the current
faceless Ideas Hub profiles resolve to `generic_v1`, which checks technical integrity without
guessing game/editorial rules. The Composer profile contract is read from
`GET /v1/render-profiles`; current `youtube_1080p30_v1` means 1920×1080 at 30 fps with H.264/AAC and
48 kHz audio.

Endpoints:

- `GET /workflow/{id}/qa-report?mode=standard|deep` is side-effect free;
- `POST /workflow/{id}/qa-report?mode=standard|deep&force=false` runs/reuses QA only;
- `GET /workflow/{id}/pacote-producao` includes the compatible standard `qa_report` separately.

`force=true` reruns only QA checks over the same upstream identities. A changed audio, alignment,
plan, timeline, render, policy, strategy or mode creates another report identity.

## Final identity and transcription

Workflow state exposes duration, SHA-256, byte size, 48 kHz sample rate, mono channels, mastering
configuration and measured before/after metrics. Host paths are not returned by the public schemas.
Transcription is upserted by final audio SHA-256. `FasterWhisperNarrationProvider` serializes model
access and uses the explicit `resident` (default) or `per_job` lifetime policy (CPU/int8 by default);
a transcription failure does not corrupt
or invalidate a safely completed final WAV.

## Configuration

Copy `.env.example`. Important settings include:

- `VOICE_API_URL`, `VOICE_PROFILE`, `VOICE_GENERATION_PROFILE`, `VOICE_LANGUAGE`, `VOICE_SEED`;
- separate create/status/download/overall timeouts and bounded HTTP retry/backoff;
- bounded quality retries, deterministic retry-seed provenance and conservative source-block
  duration limits;
- versioned Portuguese text normalization before the pronunciation glossary;
- `PRONUNCIATION_GLOSSARY_PATH` and the explicit `PRONUNCIATION_LOCALE=pt-BR` context;
- `SILENCIO_ENTRE_BLOCOS_MS`, `AUDIO_FINAL_SAMPLE_RATE`;
- LUFS/True Peak policy and duration tolerance;
- `TRANSCRIPTION_MODEL`, `TRANSCRIPTION_DEVICE`, `TRANSCRIPTION_COMPUTE_TYPE` and optional
  `TRANSCRIPTION_LANGUAGE` (blank means Faster-Whisper autodetection; the clone benchmark uses
  its independent `--asr-language pt` default).
- the frozen production voice/profile/seed contract and normal workflow command are documented in
  [`backend/docs/PRODUCTION_VOICE_BASELINE.md`](backend/docs/PRODUCTION_VOICE_BASELINE.md).
- `TRANSCRIPTION_MODEL_LIFETIME=resident|per_job`; bounded audio behavior and measurement details
  are documented in [`backend/docs/RESOURCE_HARDENING.md`](backend/docs/RESOURCE_HARDENING.md).
- alignment strategy, fuzzy/ambiguity thresholds, minimum speech coverage and complexity guards.
- versioned narration speech-quality policy and repeated-ASR-run threshold.
- `MEDIA_INDEXER_API_URL`, timeout/retry settings and the bounded Visual Director search policy.
- Production Timeline strategy/schema versions, 0.5 s minimum visual duration, bounded source
  fallback and deterministic gap-fill/coalescing policy.
- `VIDEO_COMPOSER_API_URL`, bounded HTTP retry/backoff, one-shot poll interval, overall render
  timeout and the `youtube_1080p30_v1` final profile.
- QA strategy version, bounded Celery dependency retries and the 10 GiB default deep-stream guard.

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
The separately deployed Video Composer is resolved through `VIDEO_COMPOSER_API_URL_DOCKER`
(default host port 8013); it is intentionally not managed by this Compose project.

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

`GET /health` is liveness. `GET /health/dependencias` independently verifies PostgreSQL, Redis,
Ideas Hub, Voice Service, Media Indexer and Video Composer readiness. It reports `ready` only when
all dependencies are healthy and otherwise reports `degraded` with the unavailable boundary;
dependency failure never changes process liveness. Dashboard queue measurement reads the real
Kombu Redis list `audio_producao`; an unavailable Redis is represented as `fila_status=unavailable`
and `fila_tamanho=null`, never as a falsely empty queue.

The frontend is an operator console over both state models. It preserves the legacy audio lifecycle
while presenting the canonical Stage 17 production status/phase, a production timeline, dependency
health and an attention queue for blocked/failed/retry/publication states. Polling is serial
(request, response, bounded delay), aborts on unmount and applies bounded backoff; expected artifact
404s mean “not created yet”, while 5xx, malformed responses and network failures stay visible.
Dashboard `duracao_media_audio_bloco_segundos` is explicitly physical audio duration. Processing
performance is shown only from measured take/workflow/render timestamps (latency p50/p95, retries,
failed takes, attempts, completion time, render factor and QA rate).

For an already prepared, current, gap-free workflow, the opt-in real boundary smoke is:

```bash
cd backend
.venv/bin/python scripts/smoke_stage17_orchestration.py --workflow-id <uuid>
```

It resumes the production if allowed, observes the durable workflow, downloads the final stream
without buffering it in memory, and reports `STAGE17_ORCHESTRATION_RENDER_SMOKE`.

Stage 18 standard/deep smoke uses an existing completed workflow and reports exact counters:

```bash
cd backend
.venv/bin/python scripts/smoke_stage18_qa.py --workflow-id <uuid>
.venv/bin/python scripts/smoke_stage18_qa.py --workflow-id <uuid> --mode deep --force
```

The complete integration/contract smoke is also opt-in and has a strict plan mode. It neither
deletes data nor judges creative quality. With `--run`, it creates a workflow only from the exact
approved script ID (or observes the exact workflow ID), follows durable phase transitions, validates
alignment/plan/timeline/render contracts and executes standard Stage 18 QA. A blocked existing
workflow is not resumed unless `--resume-blocked` is explicitly supplied.

```bash
cd backend
.venv/bin/python scripts/smoke_production_e2e.py --roteiro-id <uuid>
.venv/bin/python scripts/smoke_production_e2e.py --roteiro-id <uuid> --run \
  --report production-e2e-smoke.json
```

Use the first command to inspect the non-mutating plan. Run the second only in a prepared environment
after the voice benchmark; it can invoke every real production dependency and create durable rows.

## Production handoff

`GET /workflow/{id}/pacote-producao` returns script blocks, structured `visual_queries`, logical
audio URLs, block/final objective metadata, mastering provenance, hash-bound transcription and a
separate compatible `narration_alignment` section. When available, `visual_plan` is another separate
derived section containing gameplay source windows and alternatives; the original VisualQueries are
not rewritten. `production_timeline` remains a separate derived section containing the narration
track, exact gameplay trims, primary-track placements, explicit gaps and renderer-facing metrics.
`render` is a separate top-level section containing only the current Composer mirror and validated
output metadata; it is never embedded into the timeline. `qa_report` is another separate section
and exposes checks/metrics but no MP4 bytes, host paths, stack traces or temporary deep-QA path.
Older scripts without `visual_queries` remain valid and return an empty list. See
`backend/docs/HANDOFF_VISUAL_PRODUCTION.md`.

## Limits

This stage is single-worker/single-queue oriented. The Voice Service independently owns its bounded
single-GPU queue. Alignment uses deterministic text/ASR evidence, not a forced-alignment model; very
large blocks are rejected by configured token/cell guards. The Visual Director supports gameplay
sources only and depends on existing searchable Media Indexer indexes. The Production Timeline
supports one narration track and one primary gameplay track. Stage 16 owns source materialization,
frame quantization, Remotion and output validation; Stage 17 does not copy the MP4. Remote
cancellation is unavailable, so local cancellation merely prevents stale polling from promoting a
later result. The observed local Media Indexer dataset still lacks a genuinely usable completed
searchable gameplay chain, so full real gameplay editorial-quality validation remains external
validation debt. Automated QA proves technical/artifact consistency and structural coverage, not
that selected footage is editorially excellent. No automatic publishing or repair workflow exists.
The current frontend dependency audit still reports 3 moderate and 1 high advisory; the available
automated fixes require breaking Vite/React Router upgrades, so that inherited debt was documented
rather than changed inside the frozen production stage. The Celery container also currently runs as
root and emits Celery's security warning; hardening its runtime UID requires a separate volume/
permissions review.
See `backend/docs/NEOVIDS_PRODUCTION_ARCHITECTURE.md`, `backend/docs/ROADMAP_STATUS.md` and
`backend/docs/MANUAL_QUALITY_CHECKLIST.md`.
