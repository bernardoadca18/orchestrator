# PresentationSpec V1

PresentationSpec is a frozen Stage 17 render input, not a new roadmap stage. ProductionTimeline
continues to decide which source appears and when. The Orchestrator resolves overlay presentation;
Video Composer only validates, quantizes, and renders it.

GET /workflow/{workflow_id}/composer-render-input returns composer_render_input_v1 with the
compatible ProductionTimeline and an optional strict presentation_spec_v1. Absence is the
historical rendering behavior.

## Captions

caption_policy_v1 consumes only TranscricaoAudio.palavras_timestamps after verifying
TranscricaoAudio.audio_sha256 == WorkflowExecucao.audio_final_sha256. It validates finite,
monotonic word ranges against the same 250 ms bound used by NarrationAlignment. Caption projection
does not mutate canonical ASR evidence: exact zero-duration points caused by millisecond persistence
rounding receive a derived 1 ms interval, overlaps up to 250 ms advance only the derived start, and
ends up to 250 ms beyond physical narration duration are clamped. A correction that consumes the
word interval, or any larger violation, remains a hard input failure with structured diagnostics.
Segmentation uses fixed pause, duration, punctuation, and two-line/42-character limits. No ASR or
LLM is called.

## Titles, branding, and transitions

Section text comes only from the optional script-block display_title preserved from an existing
editorial section plan. tipo, purpose, prose, and VisualQueries are never title sources. A missing
display title renders nothing.

Branding defaults to the closed branding_disabled_v1 preset. text_watermark_v1 exists for bounded
synthetic/profile fixtures and accepts only text, placement, and opacity—never an asset path or URL.

CUT remains the ProductionTimeline default. The conservative policy resolves a 0.3-second
fade_to_black overlay at explicit titled section boundaries. It is centered on the real block
boundary, bounded by narration duration, and does not alter total duration or source selection.

## Identity and recovery

The canonical PresentationSpec fingerprint covers all caption text/timing, titles, branding, and
transitions. Stage 17 request identity upgrades to stage_17_workflow_render_v2_presentation only
when presentation exists. The same fingerprint is validated by Composer and returned with its
render job. Current-render lookup, retry/resume, and completion reconciliation recompute the current
PresentationSpec identity, so stale presentation cannot reuse or promote an older render. No
database migration is needed: the existing unique request fingerprint is the durable semantic
identity and historical rows remain untouched.

Stage 18 adds deterministic structural validation of the same spec. It does not use OCR, computer
vision, or an LLM, and deep MP4 validation remains independent.
