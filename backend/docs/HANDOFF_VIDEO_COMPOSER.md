# Stage 16 renderer handoff

Stage 16 consumes a completed `production_timeline_schema_v1`. It must not repeat Media Search,
candidate selection, source trimming, gap policy or pacing decisions.

## Temporal contract

All ranges use seconds and `[start,end)` semantics.

- Timeline time is final video time and equals final narration time in v1.
- Candidate time is the complete Stage 14 gameplay window allowed for a placement.
- Source time is the exact trim inside that candidate window.

Seconds are authoritative. Stage 15 does not define FPS or quantize to frames. Stage 16 must choose
the canonical render profile once and document its frame rounding without modifying the source
second values.

## Narration track

`tracks.narration` always spans `[0,duration_seconds)` for a completed timeline. It references the
existing final WAV through `audio_url`, SHA-256, sample rate, channels and byte size. The Composer
must resolve that logical URL; it must not expect a copied timeline-owned audio file.

## Primary visual track

`tracks.primary_visual.placements` is ordered and non-overlapping. For each placement:

- `timeline_start_seconds`/`timeline_end_seconds` specify final placement;
- `source.project_id`, `session_id` and `media_id` are logical Media Indexer identities;
- `source.source_start_seconds`/`source_end_seconds` specify the exact gameplay trim;
- `source.candidate_start_seconds`/`candidate_end_seconds` prove the trim stayed within Stage 14;
- `playback_rate` is exactly `1.0`;
- `transition` is exactly `cut`;
- VisualQuery/VisualDecision IDs and provenance explain the decision.

When contiguous placements from distinct Stage 14 decisions are coalesced,
`diagnostics.allowed_candidate_ranges` retains every contributing candidate range; the public
candidate start/end becomes their continuous envelope. Coalescing is rejected unless those ranges
continuously cover the exact source trim.

Stage 16 needs a `MediaAssetResolver` that converts logical source identity into readable media.
Stage 15 intentionally neither exposes host paths nor downloads/copies media.

## Gaps

`gaps` is the exact complement of primary visual coverage over the narration duration. Each gap has
a reason, severity and related VisualQuery IDs where time provenance exists. Stage 16 must apply an
explicit product policy—fail, placeholder or another separately approved behavior. It must not
silently hold, loop or invent footage.

## Validation guarantees

Before completion, `production_timeline_validator_v1` guarantees:

- positive final duration and full narration coverage;
- finite, ordered, in-range timeline placements;
- no overlap on the primary visual track;
- exact source-duration identity at playback rate 1.0;
- every trim is contained by its recorded candidate window;
- gaps exactly cover every interval not occupied by gameplay;
- coverage and severity metrics reconcile with total duration.

## Program Audio handoff within Stage 17

The coherent composer endpoint preserves `composer_render_input_v1` for narration-only V1/V2/V3
renders. When the immutable script contains `audio_intent`, the Orchestrator emits
`composer_render_input_v2` with a fingerprinted `audio_mix_plan_v1`. That plan, not the Timeline,
freezes the concrete controlled music asset, enabled ranges, fades, gain/duck envelope, separate
program-master policy and 48 kHz stereo output. Video Composer emits V4 only for this path. SFX and
gameplay audio remain out of scope; source proxies stay silent. See `AUDIO_MIX_PLAN_V1.md`.
