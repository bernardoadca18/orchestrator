# Pronunciation and pacing v2

This record documents the 2026-08-30 audit. It does not change the frozen Qwen production voice.

## Canonical versus synthesis text

Ideas Hub and workflow snapshots keep canonical editorial spelling (`GTA VI`, `trailer`,
`hardware`). Orchestrator derives synthesis text in this order:

1. canonical text;
2. deterministic PT-BR normalization;
3. approved pronunciation resolver;
4. synthesis text sent to Qwen.

The resolver is single-pass: replacement output is never matched again. Phrase-aware title
numeral rules outrank exact phrases, which outrank tokens and literals; project-scoped rules
outrank global rules. Longest/specific matches win deterministically. Word boundaries prevent a
rule for `GTA V` from changing `GTA Vice City` or a generic `capítulo VI`.

The production lexicon contains disabled, undecided candidates for `GTA IV`, `GTA V` and `GTA VI`.
They do not affect production identity or synthesis until human review explicitly promotes one.
`trailer` and `hardware` have competing forms in `pronunciation_reference_v2`; no phonetic
spelling was guessed or promoted.

Preflight reports risky title numerals, initialisms and mixed-language terms without blocking TTS.
Real-take ASR disagreements are aggregated by canonical/observed form with occurrence count,
workflow/take/block IDs, first/last seen, context and a block-level audio preview. Aggregation is
diagnostic only and never mutates the lexicon.

```bash
cd /home/bernardo/workspaces/neovids/orchestrator/backend
.venv/bin/python scripts/pronunciation.py validate
.venv/bin/python scripts/pronunciation.py resolve --text 'GTA IV, GTA V e GTA VI; trailer, hardware, Xbox 360, NPC e Rockstar.'
.venv/bin/python scripts/pronunciation.py preflight --text 'GTA VI terá NPCs e hardware novo.'
```

Use `lab-run`, listen, then `review`/`promote` explicitly. Canonical text is never overwritten.

## Pacing A/B

The user's 1.25x observation is treated as an experiment, not a production default and never a
duration-contract repair. `benchmark_narration_tempo.py` is plan-only unless `--run` is supplied.
It derives pitch-preserving FFmpeg `atempo` candidates from an existing narration and does not run
TTS. Defaults are 1.00, 1.05, 1.10, 1.15, 1.20 and the previously heard 1.25 reference.

Each candidate identity contains implementation version, factor, input SHA, format and FFmpeg
version. Publication is atomic; raw SHA is rechecked, sample rate/channels are preserved, duration
ratio is checked, and sample clipping is rejected. The manifest distinguishes raw/assembled/mastered
durations and states that no factor was promoted.

```bash
cd /home/bernardo/workspaces/neovids/orchestrator
backend/.venv/bin/python backend/scripts/benchmark_narration_tempo.py \
  --input '<ABSOLUTE_EXISTING_NARRATION_WAV>' \
  --output artifacts/narration-tempo

# Only after reviewing the plan:
backend/.venv/bin/python backend/scripts/benchmark_narration_tempo.py \
  --input '<ABSOLUTE_EXISTING_NARRATION_WAV>' \
  --output artifacts/narration-tempo --run
```

If a tempo factor is ever promoted, it must become part of a versioned post-processing profile and
duration calibration must use mastered narration (or explicitly incorporate the factor). Existing
conservative/balanced tonal profiles were not retuned in this work.
