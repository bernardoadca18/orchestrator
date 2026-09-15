# NeoVids production architecture — Stages 1–18

## Revision and production handoff authority

- **HUMAN APPROVAL IS SOVEREIGN.** Editorial audit and critic output remain visible diagnostics;
  they do not veto an explicit operator approval.
- **HISTORICAL REVISIONS REMAIN USABLE. LATEST REVISION IS A DEFAULT, NOT AN AUTHORITY.** The
  operator selects an immutable Ideas Hub `roteiro_id`; revision age is a warning only.
- **MEDIA BINDING IS OPERATIONAL CONFIGURATION.** A metadata-only binding derived from an approved
  revision preserves that approval and does not rerun writer, critic, audit, or visual enrichment.
- **PRODUCTION WORKFLOWS PIN AN EXACT IMMUTABLE REVISION.** `WorkflowExecucao.roteiro_id` and
  `roteiro_snapshot` record the selected artifact. There is no implicit latest-revision lookup.
- Production Readiness remains a strict technical gate. `editorial_approval_missing`,
  `media_scope_missing`, and `visual_readiness_not_ready` expose distinct operator actions;
  project/session identities are validated against Media Indexer before binding.

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

The Stage 17 Composer input is ProductionTimeline + optional PresentationSpec V1 + optional
AudioMixPlan V1. PresentationSpec is resolved from final-audio transcription and explicit
editorial metadata. AudioMixPlan resolves immutable script `audio_intent` to a concrete licensed
music asset, timing, ducking, and a separate program-master policy. Both are owned by the
Orchestrator; neither is Stage 19. Their fingerprints participate in render request identity and
stale-render reconciliation. The Composer never rebuilds either from independent endpoints.

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
  -> AudioMixPlan (when audio_intent is present)
  -> WorkflowRender / Video Composer MP4
  -> ProductionQAReport
  -> release-ready artifact (or explicit failed/blocked gate)
```

Every arrow is identity-checked. Stage 18 reads the current compatible chain and writes only its
own versioned report. It does not call Qwen, Whisper, Media Search or any upstream `ensure`, and it
never creates a Composer render. Standard QA uses a one-byte range request. Deep QA is explicit and
streams a temporary MP4 for independent SHA/ffprobe validation with guaranteed cleanup.

Narration mastering is intentionally transparent gain and True Peak protection. Program Audio V1
keeps that mono narration artifact as a stem, mixes only one controlled instrumental music stem,
then measures and transparently masters the complete stereo program to a candidate policy. These
metrics and identities remain separate. Neither form of mastering can repair
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
exposing host filesystem paths. Pronunciation entries are versioned, enabled and explicitly human
approved. The compiled one-pass resolver rejects ambiguous/non-idempotent config and includes its
semantic identity in block synthesis identity. A changed identity supersedes an in-flight old take,
preserves all historical takes/WAVs and invalidates current final-audio/downstream pointers through
the existing locked take workflow. A late old result cannot promote itself.

`scripts/pronunciation.py` is the operator surface for list/show/validate/resolve, controlled Qwen
lab matrices, JSONL/WAV inspection, human review, optimistic promotion and disable. Production
never experiments. Lab failures—including Qwen's runaway guard—remain evidence, and no scorer can
auto-promote a candidate. The full contract and commands are in `docs/PRONUNCIATION_LAB.md`.

The Qwen Base voice-clone API has no native speaking-rate argument; benchmark manifests therefore
record measured words-per-minute and characters-per-second rather than applying an unsafe speed
transform.
