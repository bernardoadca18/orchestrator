# NeoVids production architecture — Stages 1–18

## Ownership map

- **Media Indexer** owns media identity, immutable notes, audiovisual indexing, Semantic Timeline
  and Media Search. It exposes logical media IDs/URIs, never host paths.
- **Ideas Hub** owns editorial intent, approved production scripts and `VisualQuery` requirements.
- **Qwen Voice Service** owns provider-neutral speech synthesis jobs and native synthesized WAVs.
- **Orchestrator** owns pronunciation-derived synthesis text, final narration assembly/mastering,
  transcription, NarrationAlignment, VisualPlan, ProductionTimeline, WorkflowRender mirroring,
  workflow recovery and the Stage 18 release gate.
- **Video Composer** owns source materialization, deterministic frame quantization/rendering,
  output validation and authoritative MP4 storage/streaming.

```text
media ingestion
  -> Semantic Timeline / Media Search
  -> script + immutable VisualQuery intent
  -> Qwen narration
  -> final mastered audio + transcription
  -> NarrationAlignment
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
