# Hybrid Visual Sources V2

Hybrid retrieval is opt-in through the approved script's static-image media scope. Legacy
scripts keep `visual_director_v1` and `production_timeline_schema_v1` byte-compatible behavior.

For an enabled project scope, Visual Director runs temporal Media Search and Image Search in
parallel under the existing concurrency limit. It fuses each branch's native ranking with
weighted reciprocal-rank fusion (`hybrid_visual_fusion_v1`); native scores are diagnostic and
are never treated as cross-modal calibrated values. Provider failures are recorded per branch,
and one healthy branch may complete the query.

`VisualPlan` V2 persists a discriminated source union:

- `video_segment`: project/session/media identity plus a real candidate/source time range;
- `static_image`: project/media/ImageSemanticIndex identity, checksum, byte size, MIME type,
  dimensions, and provenance, with no session or source timestamps.

`ProductionTimeline` V2 retains integer-microsecond planning and frame-independent time. Static
images receive timeline holds (2 s minimum, 5 s preferred, 8 s hard maximum, 15 s maximum
consecutive still sequence). Alternatives can mix modalities to cover long narration spans.
Presentation is deterministic (`static_image_presentation_v1`) and derived from aspect ratio and
placement order.

Before submission, Stage 17 checks the source union, hold/presentation limits, multimodal
diversity, and current Media Indexer metadata for every image (project, kind, checksum, size,
MIME). Stable failures block before Composer. Stage 18 selects `game_analysis_qa_v2` for hybrid
timelines and rechecks identity, source types, holds, and diversity without search, vision, or
artifact regeneration.

For a future real smoke, approve a script scoped to an existing MediaProject with at least one
ready video MediaSession and one completed ImageSemanticIndex, enable `static_images`, then run
the normal Stage 17 workflow. This is intentionally opt-in and must not be run automatically on
an operator corpus.
