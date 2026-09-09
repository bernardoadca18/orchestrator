# Production voice baseline — 2026-08-29

The normal Orchestrator narration workflow is frozen to:

- `VOICE_PROFILE=bernardo_xvec_v2`
- voice identity `c4e74a0da51bd5fbc18c4d9b0bc5623d2e4ea91ff8d873374b6c1e41632b802b`
- `VOICE_GENERATION_PROFILE=narration_v2`
- generation identity `e5433e1cb8b20f91913ad37763b891f3b327a50be2f5c7d09e9a5fdaa97f05d7`
- `VOICE_SEED=303`
- Qwen synthesis language `Portuguese`
- reference `ref_full_processed_raw.wav`, SHA-256
  `8856924e05f76d1a3a5b8554218f80b5e1a898aebfe211ec1b65b60eabdbc70b`
- clone mode `x_vector_only`
- model `Qwen/Qwen3-TTS-12Hz-1.7B-Base`, revision
  `fd4b254389122332181a7c3db7f27e918eec64e3`
- main temperature `0.95`, subtalker temperature `0.90`; all remaining controls are owned by the
  versioned Qwen `narration_v2` profile.

`narration_v1`, `bernardo_v1` and `bernardo_xvec_v1` are retained for historical reproduction.
Transport retries replay the same semantic request, idempotency key and seed. Only the existing
bounded quality retry policy derives a new, recorded seed; deterministic Qwen chunk seeds remain
provider-owned.

For every block, the workflow snapshots canonical, normalized and synthesis text; normalizer and
pronunciation identities/substitutions; voice/generation names and identities; reference/clone
provenance in the provider metadata; requested/resolved/chunk seeds; model/revision/chunking; WAV
integrity and ASR/content diagnostics. Changing any frozen semantic input produces a new synthesis
identity. Historical takes are not overwritten.

To regenerate narration for a deliberately selected existing workflow through the normal durable
path (including normalization, pronunciation, Qwen guards, physical validation, ASR, take history,
assembly, current mastering and downstream invalidation), use a unique idempotency key:

```bash
WORKFLOW_ID='<existing-workflow-uuid>'
curl -fsS -X POST "http://localhost:8011/workflow/${WORKFLOW_ID}/audio/regenerate" \
  -H 'Content-Type: application/json' \
  -H "Idempotency-Key: production-voice-freeze-20260829-${WORKFLOW_ID}" \
  -d '{"confirm":true}'
```

This action intentionally regenerates every narration block and invalidates downstream artifacts;
inspect the workflow ID before running it. Creating a workflow from an approved script through
`POST /workflow` also uses these defaults automatically.

Known limitations remain: speaker drift can occur, ASR is not speaker verification, pronunciation
promotion stays a separate reviewed workflow, and post-processing is not part of this freeze.

As of 2026-09-09, this limitation is observable rather than silent: Qwen emits warning-only
chunk-level speaker similarity diagnostics and the Orchestrator stores them on durable
`AudioChunkTake` rows. This did not change either frozen production identity or its sampling
controls. Experimental non-streaming and ICL profiles have new identities and were not promoted.

Block materialization is now bound to a deterministic manifest of active physical chunk takes.
Blocks stay native 24 kHz PCM16 with sample-exact chunk/pause verification; final narration alone
performs the single resample to 48 kHz before transparent gain-only mastering.

The pinned runtime deliberately uses `sdpa`; `flash-attn is not installed` is therefore an optional
capability warning, not a readiness failure. The production artifact path uses the in-process WAV
stack and does not invoke the SoX executable; an upstream `sox: not found` capability probe is also
non-blocking for this path. Neither warning justifies changing attention or installing an unused
binary merely to silence startup output.
