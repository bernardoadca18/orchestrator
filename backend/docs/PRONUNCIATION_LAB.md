# Pronunciation resolution and Pronunciation Lab

This is an operational Orchestrator subsystem inside the frozen NeoVids production architecture.
It is not a new roadmap stage.

## Ownership and data flow

```text
immutable canonical script
  -> conservative pt-BR normalization
  -> approved deterministic pronunciation lexicon
  -> synthesis text + replacement provenance
  -> Qwen Voice Service
```

The Orchestrator owns every pronunciation decision. The Qwen service receives the already resolved
synthesis text and remains a provider-neutral synthesis service. It does not load this lexicon,
rewrite words, emulate SSML, choose a pronunciation or perform a hidden quality retry.

`AudioBloco.texto` remains canonical. `normalized_text` and `synthesis_text` are derived values.
Each matched occurrence records entry ID/revision, lexicon version, category, match policy, input
and output spans, original/replacement text and approval/source metadata in
`pronunciation_replacements`. Migration `012` snapshots canonical/normalized/synthesis text and
pronunciation/normalizer provenance on every new `AudioBlocoTake`. Existing takes remain nullable;
they are not backfilled or reinterpreted with fabricated historical decisions.

## Production lexicon and matching

The source of truth is `config/pronunciation_glossary.yaml`. Its v2 schema supports stable IDs,
canonical and synthesis forms, category, locale, token/literal policy, case policy, enabled state,
priority, entry revision, origin, human approval, notes/confidence and optional project scopes.
Categories are `proper_name`, `game_title`, `brand`, `technical_term`, `acronym`, `initialism`,
`abbreviation` and `custom`.

Only `enabled: true` plus `approval.status: approved` entries participate in production. An
approved entry may intentionally keep the raw spelling by making both forms equal. That decision
is traceable without changing the text.

The resolver normalizes input and lexicon terms to Unicode NFC, compiles active rules once per
project/locale and resolves in one pass. Token mode protects Unicode word boundaries. Literal mode
is explicit and may match inside a token. Project-scoped rules override global rules. Otherwise,
higher priority wins and longer terms win at equal priority. Equal-priority incompatible duplicate
rules fail validation. Replacement output is never recursively fed back into matching; configs
whose output would trigger another mutation are rejected as non-idempotent.

The production identity is a SHA-256 of resolver version and the canonicalized semantics (including
entry revisions) of the selected active entries. YAML order, comments, release-label version and
notes do not change it. Replacement, entry revision, locale, matching, priority, scope or approval
changes do. The lexicon release version is still recorded in per-occurrence provenance. The
production identity is part of each block synthesis identity alongside normalizer, voice,
generation, chunking, language and seed identities.

When a block identity changes, the Orchestrator supersedes an outstanding old request under the
existing locks, retains previous takes/WAVs as history, clears current final-audio pointers and
queues downstream production from the audio boundary. A late superseded result cannot become the
current take.

## Inspect, validate and preview

Run commands from `orchestrator/backend`:

```bash
.venv/bin/python scripts/pronunciation.py validate
.venv/bin/python scripts/pronunciation.py list
.venv/bin/python scripts/pronunciation.py list --all
.venv/bin/python scripts/pronunciation.py show brand.rockstar-games
.venv/bin/python scripts/pronunciation.py resolve \
  --text 'A Rockstar Games mostrou GTA 6.'
```

`resolve` calls neither Qwen nor CUDA. It prints canonical, normalized and synthesis text, both
identities and every rule/span that would fire.

## Controlled laboratory runs

The lab is deliberately separate from production. Manual candidates are always supported.
`--initialism-candidates` adds deterministic experimental raw, spaced-letter and pt-BR letter-name
forms; these are candidates, never automatically accepted linguistic truth. A
`PronunciationCandidateSource` protocol is the extension point for a future LLM/phonetic proposer.
Production never depends on such a proposer.

```bash
.venv/bin/python scripts/pronunciation.py lab-run \
  --target 'Rockstar Games' \
  --context 'O jogo foi desenvolvido pela {target} e recebeu várias atualizações.' \
  --candidate 'Rockstar Games' \
  --candidate 'OPERADOR_DEFINE_CANDIDATO' \
  --language Portuguese \
  --voice bernardo_v1 \
  --generation narration_temp080 \
  --seeds 101

.venv/bin/python scripts/pronunciation.py lab-run \
  --target GTA \
  --context 'A análise de {target} começa agora.' \
  --initialism-candidates \
  --language Portuguese \
  --voice bernardo_v1 \
  --generation narration_temp080 \
  --seeds 101
```

Every cell keeps context, language, voice, generation profile and seed fixed; only the target
representation changes. A semantic request fingerprint becomes the Qwen `Idempotency-Key`, so an
identical failed request resolves to the same durable Qwen job instead of silently burning another
quality attempt. Transport retry may repeat that same request. The lab itself makes exactly one
quality attempt per matrix cell. Context, candidate, seed and total-cell limits reject accidentally
large GPU-burning matrices before any request is sent.

Each run creates `pronunciation-lab-results/<run-id>/run.json`, `manifest.jsonl`, `summary.json` and
successful WAVs. The manifest retains successful and failed cells, requested/resolved/chunk seeds,
profile/model/chunking identities, duration, latency, quality-guard state, safe errors, relative
WAV reference and checksum. A `chunk_quality_guard` rejection is a valid failed benchmark result.

Machine scoring is optional through `PronunciationScorer`; the default identity is
`manual_review_only_v1` and loads no recognition model. Do not add a large scorer dependency to
normal production or unit tests. Human listening remains authoritative.

## Review and production promotion

```bash
.venv/bin/python scripts/pronunciation.py lab-show pronunciation-lab-results/RUN_ID

.venv/bin/python scripts/pronunciation.py review \
  pronunciation-lab-results/RUN_ID CANDIDATE_ID \
  --status approved --reviewer bernardo --notes 'Listened in the controlled contexts.'

# Preview only; this does not edit production config.
.venv/bin/python scripts/pronunciation.py promote \
  pronunciation-lab-results/RUN_ID CANDIDATE_ID \
  --entry-id brand.rockstar-games --category brand

# Copy configuration_identity from `validate`, then explicitly apply.
.venv/bin/python scripts/pronunciation.py promote \
  pronunciation-lab-results/RUN_ID CANDIDATE_ID \
  --entry-id brand.rockstar-games --category brand \
  --expected-config-identity CURRENT_CONFIGURATION_SHA256 --apply
```

Promotion requires the candidate's latest review to be `approved` and at least one successful Qwen
sample. A rejected or undecided candidate cannot be promoted. `--apply` additionally requires the
exact configuration identity that the operator reviewed. The CLI locks, re-reads, validates and
atomically replaces the YAML; unrelated entries remain semantically unchanged. Updating an
existing stable ID increments its entry revision. Git remains the durable review/history mechanism
for production config, while completed database rows retain the exact old replacement provenance.

Disable follows the same preview/optimistic-apply pattern:

```bash
.venv/bin/python scripts/pronunciation.py disable brand.rockstar-games
.venv/bin/python scripts/pronunciation.py disable brand.rockstar-games \
  --expected-config-identity CURRENT_CONFIGURATION_SHA256 --apply
```

## Opt-in real-Qwen progression

Do not run a large matrix first. In Portuguese with `bernardo_v1`, start each target with raw
spelling plus a small operator-chosen candidate set under `narration_temp080` and seed 101:

```bash
.venv/bin/python scripts/pronunciation.py lab-run --target 'Rockstar Games' \
  --context 'O jogo foi desenvolvido pela {target}.' --candidate 'Rockstar Games' \
  --generation narration_temp080 --seeds 101
.venv/bin/python scripts/pronunciation.py lab-run --target 'Red Dead Redemption' \
  --context 'Hoje vamos analisar {target}.' --candidate 'Red Dead Redemption' \
  --generation narration_temp080 --seeds 101
.venv/bin/python scripts/pronunciation.py lab-run --target 'Unreal Engine' \
  --context 'O projeto utiliza {target} para renderização.' --candidate 'Unreal Engine' \
  --generation narration_temp080 --seeds 101
.venv/bin/python scripts/pronunciation.py lab-run --target 'ray tracing' \
  --context 'A placa ativa {target} durante o jogo.' --candidate 'ray tracing' \
  --generation narration_temp080 --seeds 101
.venv/bin/python scripts/pronunciation.py lab-run --target GTA \
  --context 'A análise de {target} começa agora.' --initialism-candidates \
  --generation narration_temp080 --seeds 101
.venv/bin/python scripts/pronunciation.py lab-run --target NPC \
  --context 'Cada {target} reage ao jogador.' --initialism-candidates \
  --generation narration_temp080 --seeds 101
.venv/bin/python scripts/pronunciation.py lab-run --target GPU \
  --context 'A {target} processa os gráficos.' --initialism-candidates \
  --generation narration_temp080 --seeds 101
.venv/bin/python scripts/pronunciation.py lab-run --target CPU \
  --context 'A {target} controla esta etapa.' --initialism-candidates \
  --generation narration_temp080 --seeds 101
```

Add candidates only after defining them intentionally; no spelling in this document is declared a
winner. Discard obvious/guard failures, then run survivors at seed 202, followed by seed 303 only
for finalists. Repeat finalists under `narration_v1` for the 0.9 baseline while holding all other
inputs fixed. Temperature 0.7 is not a primary candidate. Record every quality failure.

Benchmark a surviving term at sentence start, mid-sentence beside Portuguese, beside another
English term and before punctuation. One representation is not assumed to generalize across all
contexts.

## Deliberate limits and forbidden shortcuts

- Do not lower global temperature as a pronunciation fix.
- Do not phoneticize whole English phrases when one small span is problematic.
- Do not edit canonical script text to satisfy TTS.
- Do not add NeoVids rewriting to `qwen3-tts-workshop`.
- Do not automatically promote a scorer winner.
- Do not add hidden Qwen retries or bypass its quality guard.
- Do not synthesize English spans separately and splice normal utterances. Span synthesis remains
  a possible future exceptional fallback because it risks timbre, prosody and timing discontinuity.
