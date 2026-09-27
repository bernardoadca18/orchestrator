# Production voice baseline — 2026-09-17

Promoted from a human blind reference shootout on 2026-09-17.
PT winner: PT_A. EN primary winner: EN_B. EN reserve: EN_A.

- Model: `Qwen/Qwen3-TTS-12Hz-1.7B-Base`
- Revision: `fd4b254389122332181a7c3db7f27e918eec64e3`
- Generation profile: `narration_v2`
- Generation identity: `e5433e1cb8b20f91913ad37763b891f3b327a50be2f5c7d09e9a5fdaa97f05d7`
- Production seed: `303`
- Runtime, sampling, chunking and audio QA remain unchanged.

## PT-BR production

- Profile: `bernardo_pt_icl_v1`
- Reference: `ref_pt_br_xvec_v4.wav`
- Reference transcript: `ref_pt_br_xvec_v4.txt`
- SHA-256: `6d2df3e475cf58c7c14a5e70f8b345837a965c159dc6d79d4e8204d1e535dde7`
- Clone mode: `icl`
- Language: `Portuguese`
- Default: YES; required production asset.

## PT-BR previous production

- Profile: `bernardo_xvec_v4`
- Reference: `ref_pt_br_xvec_v4.wav`
- SHA-256: `6d2df3e475cf58c7c14a5e70f8b345837a965c159dc6d79d4e8204d1e535dde7`
- Voice identity (VoiceProfileRegistry): `dbffe30a63d4db53f6035005b542034ba36fb7e5f5f6b65f66b3f2c32c014c22`
- Clone mode: `x_vector_only`; no reference transcript.
- Language: `Portuguese`
- Default: NO; historical baseline retained for comparison and rollback.

## EN-US production

- Profile: `bernardo_en_xvec_v2`
- Reference: `ref_en_us_xvec_v2.wav`
- SHA-256: `a2b84a1596e64f69cc81bde39e6fa2efe7823318f655f4ca39ad1f1082d7b52a`
- Voice identity (VoiceProfileRegistry): `793a34345d59b366fd6b935b0749b2d640635e19e0184dc3777991d6d28c6515`
- Clone mode: `x_vector_only`; no reference transcript.
- Language: `English`
- Default: YES; required production asset.

## EN-US reserve

- Profile: `bernardo_en_xvec_reserve_v1`
- Reference: `ref_en_us_xvec_reserve_v1.wav`
- SHA-256: `a29f4e337bc1b41ba7bf2650290857a22581531717b92088b10226aabdf728cc`
- Voice identity (VoiceProfileRegistry): `1175f36369633453355ae71adbeedd52b8052c2df9b65daed52c5da235b83ac2`
- Clone mode: `x_vector_only`; no reference transcript.
- Language: `English`
- Default: NO; optional reserve.

`bernardo_xvec_v3`, `bernardo_en_xvec_v1` and all earlier profiles remain historical,
with their original configuration and assets unchanged. The optional EN V1 reference
is absent in this local checkout, so the registry does not expose EN V1 at runtime.
Both new production assets are required: missing or corrupt PT V4 / EN V2 fails startup.

Duration calibration uses `accepted_take_speaking_rate_v2`, the resolved voice and
production profile, and the immutable workflow snapshot locale. Only PT accepts
historical snapshots without a locale. New voices initially use the existing fallback
rates until at least three accepted takes exist; older voices are never relabeled.
Ideas Hub's local fallback is `speaking_rate_fallback_v2` with locale-specific identity.

## Historical baseline record — 2026-09-13

# Production voice baseline — 2026-09-13

The normal Orchestrator narration workflow is frozen to:

- `VOICE_PROFILE=bernardo_xvec_v3`
- voice identity `bf047b087b09e7a2566a1282a8a9eeb4a043ef44051b2030859ca94ae46dde4f`
- `VOICE_GENERATION_PROFILE=narration_v2`
- generation identity `e5433e1cb8b20f91913ad37763b891f3b327a50be2f5c7d09e9a5fdaa97f05d7`
- `VOICE_SEED=303`
- Qwen synthesis language `Portuguese`
- reference `ref_full_processed_raw_normalized.wav`, SHA-256
  `11b7b30c41c3d365c4122a1549cd505d16876219f75fa213f87fa15e341be546`
- clone mode `x_vector_only`
- model `Qwen/Qwen3-TTS-12Hz-1.7B-Base`, revision
  `fd4b254389122332181a7c3db7f27e918eec64e3`
- main temperature `0.95`, subtalker temperature `0.90`; all remaining controls are owned by the
  versioned Qwen `narration_v2` profile.

`narration_v1`, `bernardo_v1`, `bernardo_xvec_v1` and `bernardo_xvec_v2` are retained for
historical reproduction. V3 was selected by the controlled human reference benchmark and keeps
the transcript-free `x_vector_only` semantics.
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
