# Stage 18 Automated QA / Generalization handoff

Stage 17 ends with one durable, identity-checked production and exposes the evidence needed by a
future QA stage. This document is a boundary contract, not a Stage 18 implementation.

## Available artifacts

- immutable `WorkflowExecucao.roteiro_snapshot` and Ideas Hub IDs;
- final narration URL, SHA-256, byte size, duration, sample rate, channel count and mastering
  measurements;
- compatible versioned `NarrationAlignment` with block/query coverage metrics;
- compatible versioned `VisualPlan`, source provenance, alternatives and gap diagnostics;
- validated `ProductionTimeline`, exact gameplay trims, placement/gap coverage and strategy
  fingerprints;
- durable `WorkflowRender` mirror with Composer/profile/manifest identities, progress, result
  checksum, duration, resolution, FPS, codecs, frame count and render timing metrics;
- for Program Audio V1, AudioMixPlan/music identities and separate pre/post program-master
  measurements under the `programAudio` metrics namespace;
- controlled `GET /workflow/{id}/video` MP4 stream with byte-range support.

Every artifact remains separate. QA can trace a rendered frame window back through the render's
ProductionTimeline fingerprint, placements, VisualDecisions and Media Indexer logical media IDs.
The Orchestrator never exposes Composer host paths or persists a second MP4.

## Existing quality signals

Audio already reports Integrated LUFS, sample peak, True Peak, duration and identity. Narration
alignment reports canonical/synthesis/ASR coverage and ambiguous/unresolved query counts. The
Visual Director reports resolution strategy and source scores. Production Timeline reports visual
coverage, explicit gap duration/severity, cut count and average shot duration. Video Composer
reports render/validation timing and render factor. Narration-only profiles retain one mono AAC
48 kHz stream. The Program Audio profile requires one stereo AAC 48 kHz stream and adds distinct
plan/music identity, format, loudness, True Peak, and program-master checks without reinterpreting
narration mastering metrics.

## Preconditions and deferred work

A production is Stage 18-eligible only when `production_status=completed`, its current semantic
`WorkflowRender.status=completed`, and the render still references the current gap-free timeline.
The current local environment still needs a usable real gameplay Media Indexer chain for visual
quality evaluation. Stage 18 may define automated technical/content QA, generalization fixtures and
promotion policy; it must not silently rewrite editorial decisions or weaken the identity chain.
