# AudioMixPlan V1 and Program Audio operations

Program Audio V1 belongs to the existing Stage 17 render and Stage 18 QA flow. It is not a new
roadmap stage. Ideas Hub owns the per-block `audio_intent`; the Orchestrator resolves that intent
into one concrete, fingerprinted `audio_mix_plan_v1`; Video Composer executes the frozen plan
without editorial selection.

## Contract and identity

`composer_render_input_v2` adds a required AudioMixPlan to the legacy coherent render package.
The plan uses `longform_program_audio_v1` and the candidate
`music_mix_profile_v1_candidate`. It freezes:

- authoritative duration and narration SHA/48 kHz/mono expectations;
- enabled music ranges, immutable asset metadata, source range, loop policy, gain adjustment,
  fades, and closed transition presets;
- bounded speech regions plus attack, release, base and ducked gains;
- the distinct `youtube_program_master_v1_candidate` loudness policy;
- AAC 192k, 48 kHz, stereo output policy.

Python and TypeScript use sorted-key compact UTF-8 JSON and normalize integral floats before
SHA-256. The mirrored `audio_mix_plan_v1_contract.json` fixture prevents cross-service drift.
Every audible decision is included in the plan and render identities. The legacy
`composer_render_input_v1` and manifests V1/V2/V3 remain unchanged when no AudioMixPlan exists.

## Controlled music registry

The configured registry is `MUSIC_REGISTRY_PATH` (default `/data/music/registry.json`). Source
files live below `MUSIC_LIBRARY_ROOT` and are named exactly `<sha256>.wav` or
`<sha256>.flac`. The API never accepts or returns a host path, external URL, or filter expression.
Only the workflow's currently selected asset is served, through a controlled endpoint that verifies
asset ID, requested SHA, byte size, and the file's streamed SHA-256.

A registry entry records immutable technical identity, measured integrated LUFS/True Peak,
canonical mood/style/energy metadata, loopability, and mandatory provenance/license fields.
Program Audio V1 supports instrumental tracks only and requires
`monetization_allowed=true`. Reusing an asset ID, even for the same bytes, is rejected; a content
revision needs a new asset ID and SHA. `workflow_overrides` is a durable, auditable map from
workflow UUID to asset ID. An override wins over scoring but remains fully frozen in plan identity.

Copy `backend/config/music_registry.example.json` to the configured external music root before
enabling music. Do not commit real music.

## Adding one owned/licensed track

1. Confirm the license permits monetization and record owner, source/reference, license type, and
   required attribution. Program Audio cannot infer licensing.
2. Use lossless WAV or FLAC. Measure it without modifying the original:

   ```bash
   sha256sum /absolute/input/track.wav
   stat --format='%s' /absolute/input/track.wav
   ffprobe -v error -show_entries format=duration:stream=codec_name,sample_rate,channels \
     -of json /absolute/input/track.wav
   ffmpeg -hide_banner -nostats -i /absolute/input/track.wav \
     -af loudnorm=I=-23:TP=-2:LRA=11:print_format=json -f null -
   ```

3. Copy the original to `/data/music/<sha256>.wav` (or `.flac`) and make it read-only to the
   Orchestrator containers.
4. Add a strict registry entry. Use a new stable `asset_id`, the exact SHA/size/format/duration,
   the measured input LUFS/True Peak, canonical lowercase tags, energy 0..1, instrumental
   `vocals=false`, and truthful loopability/licensing fields.
5. Optionally set `workflow_overrides.<workflow-uuid>` to that asset ID. Restart/reload the
   Orchestrator and request the composer render input; invalid registry or incompatible intent
   fails closed before final rendering.

## Resolution, mix, and mastering

The resolver considers only music-enabled blocks, normalizes requested tags, rejects vocals, then
scores mood overlap, style overlap, and bounded energy distance. It sorts by score and asset ID;
filesystem order never participates. No compatible asset is a controlled
`music_resolution_failed`.

The Orchestrator turns word timestamps tied to the canonical narration SHA into sorted, clamped
speech regions. Gaps no longer than 350 ms merge; longer pauses remain. The list is limited to 512
regions. It freezes `-9 dB` base music, `-20 dB` during speech, 250 ms attack, 500 ms release,
small bounded energy adjustments, and deterministic short fades at music-enabled boundaries.
Unknown transition strings fail; only closed `none_v1` and `smooth_v1` presets execute.
Explicit music-off blocks contain no enabled segment. SFX intent remains preserved upstream but is
not executed.

Video Composer verifies and caches the immutable source, creates a lossless 48 kHz stereo
music-normalized derivative, creates a lossless pre-master mix, and separately applies transparent
two-pass FFmpeg loudness normalization to the full program. Cache identity includes source SHAs,
plan/policy identity, and FFmpeg identity; sidecar SHA/size validation causes corrupt entries to be
rebuilt atomically. A non-loopable track that cannot cover its enabled ranges fails. Looping, when
allowed, always starts at zero with no stretch or random offset.

The current candidate master targets -14 LUFS, -1 dBTP, and LRA 11. These settings are candidates,
not human-frozen. The narration stem remains the existing independently mastered -16 LUFS mono
artifact and is never rewritten. Remotion emits video-only for V4; the mastered PCM program is
encoded to AAC exactly once during final mux.

## Stage 18 and failure semantics

The Composer records plan/music identity, cache hits, pre/post master measurements, tool identity,
output format, and master targets in the existing render metrics JSON namespace
`programAudio`. No database migration is needed: the complete V4 manifest remains in Composer
SQLite and the Orchestrator's durable request fingerprint/metrics fields retain identity and
measurements. Changed plan, asset SHA, gain, ducking, master policy, or channel policy cannot reuse
or promote an old render.

Stage 18 preserves narration-stem checks and adds distinct plan identity, music identity, program
format, loudness, True Peak, and master identity checks. Deep ffprobe expects exactly one AAC
48 kHz stereo stream for the program profile. Failures use the stable categories
`audio_mix_plan_invalid`, `music_asset_unavailable`, `music_asset_integrity_failed`,
`music_resolution_failed`, `program_mix_failed`, `program_master_failed`, and
`program_audio_validation_failed`. A program-audio failure leaves narration, transcription,
alignment, VisualPlan, and ProductionTimeline reusable.

## Technical and human review

Run the synthetic end-to-end smoke sequentially:

```bash
npm run build
SMOKE_HYBRID=1 SMOKE_PRESENTATION=1 SMOKE_PROGRAM_AUDIO=1 npm run smoke:render
```

The smoke uses generated tones only. It verifies video/profile invariants, a single AAC 48 kHz
stereo stream, silent gameplay proxies, measured music presence and speech-region attenuation,
candidate LUFS/True Peak, stable identities, and second-job cache hits.

For the first real licensed track, compare the short lossless A/B/C artifacts:
`narration_only.wav`, `mixed_pre_master.wav`, and `mixed_program_master.wav`, together with
their manifest. Review voice dominance, fade boundaries, pause recovery, and transparency. Do not
promote the candidate gains/master target as human-frozen until that listening review is complete.

