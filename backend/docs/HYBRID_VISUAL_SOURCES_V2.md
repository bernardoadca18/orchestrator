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
MIME). Stable failures block before Composer. Stage 18 resolves editorial profile requirements
independently from structural timeline capability:

| Production profile | Timeline V1 | Timeline V2 |
| --- | --- | --- |
| `game_analysis` | `game_analysis_qa_v1`, `gameplay` | `game_analysis_qa_v2`, `video_segment \| static_image` |
| known faceless profile | `generic_v1`, `gameplay` | `generic_hybrid_v2`, `video_segment \| static_image` |
| `None` or future/unknown profile | `generic_v1`, `gameplay` | `generic_hybrid_v2`, `video_segment \| static_image` |

The generic hybrid policy preserves generic V1 editorial requirements and adds only V2
structural invariants. Game analysis retains transcription, alignment, VisualPlan, and
required-query gates. Stage 17 and Stage 18 receive the same configured 2/8/15-second hard
limits; the Composer renders the frozen hold without duplicating the tunable editorial policy.

The QA engine strategy remains `production_qa_v2`. Reuse is safe because the request fingerprint
includes the complete `policy_identity`, ProductionTimeline fingerprint, render identity, QA
mode, and engine strategy. Any material policy or timeline change creates a distinct report; the
engine name is not cosmetically re-versioned.

Migration `019` keeps its forward schema unchanged. Its downgrade is intentionally fail-closed:
if any V2 ProductionTimeline, `static_image`, `video_segment`, or other non-V1 placement exists,
`019 -> 018` aborts before DDL or data mutation and reports exact incompatible row counts. An
administrator must explicitly migrate or remediate those historical artifacts before retrying.
Editing the already-applied revision is limited to this reverse-path safety fix; no applied
forward operation or stored row is changed.

For a future real smoke, approve a script scoped to an existing MediaProject with at least one
ready video MediaSession and one completed ImageSemanticIndex, enable `static_images`, then run
the normal Stage 17 workflow. This is intentionally opt-in and must not be run automatically on
an operator corpus.
