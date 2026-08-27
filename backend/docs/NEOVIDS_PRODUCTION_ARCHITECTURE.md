# NeoVids production architecture — Stages 1–18

## Ownership map

- **Media Indexer** owns media identity, immutable notes, audiovisual indexing, Semantic Timeline
  and Media Search. It exposes logical media IDs/URIs, never host paths.
- **Ideas Hub** owns editorial intent, approved production scripts and `VisualQuery` requirements.
- **Qwen Voice Service** owns provider-neutral speech synthesis jobs, internal chunking, per-chunk
  waveform quality guards and native synthesized WAVs. It performs no hidden synthesis retry.
- **Orchestrator** owns deterministic Portuguese normalization and pronunciation-derived synthesis
  text, explicit quality retries, final narration assembly/mastering,
  transcription, NarrationAlignment, VisualPlan, ProductionTimeline, WorkflowRender mirroring,
  workflow recovery and the Stage 18 release gate.
- **Video Composer** owns source materialization, deterministic frame quantization/rendering,
  output validation and authoritative MP4 storage/streaming.

```text
media ingestion
  -> Semantic Timeline / Media Search
  -> script + immutable VisualQuery intent
  -> normalized/glossary-derived synthesis text
  -> Qwen narration with internal chunk guards
  -> final mastered audio + transcription
  -> NarrationAlignment + versioned speech-quality gate
  -> VisualPlan
  -> ProductionTimeline
  -> WorkflowRender / Video Composer MP4
  -> ProductionQAReport
  -> release-ready artifact (or explicit failed/blocked gate)
```

Every arrow is identity-checked. Stage 18 reads the current compatible chain and writes only its
own versioned report. It does not call Qwen, Whisper, Media Search or any upstream `ensure`, and it
never creates a Composer render. Standard QA uses a one-byte range request. Deep QA is explicit and
streams a temporary MP4 for independent SHA/ffprobe validation with guaranteed cleanup.

Audio mastering is intentionally transparent gain and True Peak protection. It cannot repair
repeated, missing or otherwise incorrect speech. Catastrophic Qwen chunks are rejected before
publication; any failure that escapes the chunk guard is blocked by conservative block-duration
and Faster-Whisper/alignment evidence. Diagnostic audio remains preserved.

## Release semantics

- `production_status=completed`: a current final render exists.
- compatible standard QA `publication_ready=true`: deterministic release checks passed or passed
  with warnings.
- QA `failed` or `blocked`: the render remains intact and production remains completed, but release
  is not approved.

The standard report is the normal release gate; deep QA is additional operator evidence. No social
publishing, automated repair, LLM reviewer or hidden editorial mutation is part of Stages 1–18.

## Generalization

`game_analysis_qa_v1` applies the strict current gameplay production requirements. `generic_v1`
is the safe fallback for null, unknown and current faceless profiles: it validates technical chain,
timeline, render and delivery integrity without inventing a visual/editorial contract. New profile
requirements can be introduced as a new policy/version without rewriting individual check logic.

The deterministic gate establishes technical consistency, source/query structural coverage and
render correctness. Human review against real gameplay is still required to claim pacing,
pronunciation, semantic footage quality or overall editorial excellence.

## Narration takes and regeneration

`AudioBlocoTake` is the immutable audit record for each synthesis attempt. A failed block can be
retried with `POST /workflow/{id}/audio/blocks/{ordem}/retry`; a completed or failed block can
request a distinct manual take with `/regenerate`; and a complete narration regeneration requires
`confirm=true` at `/workflow/{id}/audio/regenerate`. Every action requires a unique
`Idempotency-Key`. Existing WAVs and downstream rows are retained, while current audio pointers
and phase-aware projections are invalidated until the new chain completes. The workflow
`tentativas` counter remains execution retries; per-block `generation_attempt` and take history
describe voice attempts. A different request supersedes a queued/running take under the block
lock; a late remote result is retained as historical evidence and cannot be promoted.

Canonical script text, normalized synthesis text, glossary substitutions, model/profile identities,
chunking identity, seeds and bounded failure diagnostics are returned in workflow detail without
exposing host filesystem paths. Edit `backend/config/pronunciation_glossary.yaml`, increment its
version, and regenerate affected blocks to apply a deterministic pronunciation override. The
Qwen Base voice-clone API has no native speaking-rate argument; benchmark manifests therefore
record measured words-per-minute and characters-per-second rather than applying an unsafe speed
transform.
