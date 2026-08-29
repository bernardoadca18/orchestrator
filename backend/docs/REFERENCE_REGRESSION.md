# Voice reference regression

This is an opt-in experiment for selecting a Qwen `x_vector_only` reference. It
measures reproducible machine diagnostics and creates a listening worksheet; it
does not choose a winner, change production profiles, tune generation settings,
run post-processing, or alter the production pronunciation lexicon.

## Runtime boundary

Qwen owns `/data/references`, keeps the model resident and caches the speaker
prompt by profile identity. The benchmark-only `POST /v1/benchmark/narrations`
route accepts only a relative `reference_id`; Qwen resolves it beneath its
configured canonical directory, rejects traversal and symlink escapes, and builds
an ephemeral `x_vector_only` profile without editing `voices.yaml`. The job's
metadata stores the reference id, SHA-256 and profile identity so workers can
reconstruct the same prompt after a restart. The Orchestrator API receives a
narrow read-only mount of the canonical reference directory for discovery; no
broad host filesystem mount is used.

The normal `/v1/narrations` contract and `bernardo_v1`/
`bernardo_xvec_v1` profiles are unchanged.

## Quick and full matrices

Discovery is deterministic and includes every readable WAV (PCM16, PCM24 and
other decodable WAV subtypes); malformed files are reported and excluded. An
optional `reference_metadata.yaml` may provide `family`, `variant` and `notes`
for relative paths. No family is inferred from a filename.

Quick uses six Portuguese cases and seed `101`. Full uses all twelve cases and
seeds `101 202 303`; full requires explicit `--refs` or `--all-refs`. The
generation profile is `narration_v1`, synthesis language is `Portuguese`, ASR
language defaults to ISO `pt`, and clone mode is always `x_vector_only`.
Pass `--asr-language none` (or a blank value) only when deliberately requesting
Faster-Whisper autodetection; it is kept separate from Qwen's `Portuguese`
synthesis language.

Plan first (zero Qwen/CUDA):

```bash
python scripts/benchmark_voice_references.py --mode quick \
  --output /data/audio/benchmarks/reference-regression
```

Run explicitly:

```bash
python scripts/benchmark_voice_references.py --mode quick \
  --output /data/audio/benchmarks/reference-regression --run
```

Full shortlist:

```bash
python scripts/benchmark_voice_references.py --mode full \
  --refs ref.wav ref_minimax.wav --seeds 101 202 303 \
  --output /data/audio/benchmarks/reference-regression
python scripts/benchmark_voice_references.py --mode full \
  --refs ref.wav ref_minimax.wav --seeds 101 202 303 \
  --output /data/audio/benchmarks/reference-regression --run
```

`--resume RUN_ID` reuses successful entries only when the WAV exists and its
checksum matches. Missing or changed artifacts run again. Failed entries are
skipped unless `--retry-failed` is supplied; prior JSONL lines remain intact.
Every case is appended and fsynced as it completes, so interruption leaves a
recoverable manifest. Run ids are UTC timestamps plus random suffixes.

## Artifacts and review

Each run contains `run.json`, `references.jsonl`, append-safe `manifest.jsonl`,
`summary.json`, `summary.csv`, `listening.csv`, `listening.md`, and isolated
`refs/` WAVs. Per-case generation, artifact, guard, ASR, content and
pronunciation-disagreement states remain separate. English names/initialisms
that produce a phonetic ASR spelling are represented as inconclusive or
pronunciation disagreement rather than automatically as missing content.

`listening.csv` has one row per successful take and blank human columns. Higher
scores are better; for breath/click artifacts, 5 means no problematic artifact.
After review, summarize without modifying the machine manifest:

```bash
python scripts/summarize_reference_ratings.py RUN/listening.csv
```

Family metadata makes raw/cleaned/segment variants easy to compare descriptively;
the report never manufactures a winner. Human listening decides timbre,
naturalness, prosody, accent and preference.

## Pronunciation workflow

`config/pronunciation_reference_corpus.yaml` is versioned independently from the
reference corpus and contains realistic contexts for difficult names, acronyms
and mixed-English terms. After a reference is explicitly selected, feed it to
the existing Pronunciation Lab (still no automatic promotion):

```bash
python scripts/benchmark_pronunciation_corpus.py \
  --voice-profile bernardo_v1 --generation-profile narration_v1 --run
```

Candidates remain confined to Lab artifacts and require explicit human review
before any versioned production lexicon change. Post-processing and voice
conversion are intentionally out of scope.

Archive/share a completed run by copying its run directory as an immutable
artifact; `run.json` and the JSONL manifests contain the corpus, model,
generation, lexicon, reference and seed identities needed for reproduction.
