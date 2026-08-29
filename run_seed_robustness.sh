#!/usr/bin/env bash
set -Eeuo pipefail

# ============================================================
# NeoVids - Seed Robustness Sweep
#
# Goal:
#   Select a robust default Qwen seed AFTER reference selection.
#
# Fixed reference:
#   ref_full_processed_raw.wav
#
# Final evaluation:
#   1 reference
#   x 6 strategic cases
#   x 10 seeds
#   = 60 WAV evaluations
#
# New synthesis:
#   7 new seeds x 6 cases = 42 generations
#
# Reused:
#   seed 202 / 303    from finalist regression
#   seed 424242       from previous auditory regression
#   = 18 existing WAVs
#
# IMPORTANT:
# - Reference is fixed.
# - narration_v1 is fixed.
# - x_vector_only is fixed.
# - pronunciation configuration is fixed.
# - No parameter tuning occurs here.
# - No production profile is mutated.
# - Existing benchmark artifacts are read-only.
# ============================================================

ORCH_DIR="${ORCH_DIR:-$HOME/workspaces/neovids/orchestrator}"
QWEN_DIR="${QWEN_DIR:-$HOME/workspaces/neovids/qwen3-tts-workshop}"

REFERENCES_DIR="${REFERENCES_DIR:-/data/references}"

# New seed-sweep results go into their own namespace.
OUTPUT_ROOT="${OUTPUT_ROOT:-/data/audio/benchmarks/seed-robustness}"

# Historical reference benchmark root.
REFERENCE_OUTPUT_ROOT="${REFERENCE_OUTPUT_ROOT:-/data/audio/benchmarks/reference-regression}"

EXPORT_DIR="${EXPORT_DIR:-$ORCH_DIR/benchmark_exports}"

QWEN_READY_URL="${QWEN_READY_URL:-http://localhost:8889/health/ready}"

REFERENCE="ref_full_processed_raw.wav"

GENERATION_PROFILE="narration_v1"
SYNTHESIS_LANGUAGE="Portuguese"
ASR_LANGUAGE="pt"
CLONE_MODE="x_vector_only"

# ------------------------------------------------------------
# Strategic corpus
#
# These deliberately cover:
#
# natural_ptbr
#   general PT-BR naturalness
#
# gameplay_narration
#   actual NeoVids-style narration
#
# controlled_intensity
#   expression / prosody
#
# content_skip_canary
#   content + known speaker-drift sensitivity
#
# mixed_english
#   Portuguese/English interaction
#
# long_form
#   sustained multi-chunk stability
# ------------------------------------------------------------

CASES=(
  "natural_ptbr"
  "gameplay_narration"
  "controlled_intensity"
  "content_skip_canary"
  "mixed_english"
  "long_form"
)

# ------------------------------------------------------------
# Complete seed field used in the FINAL BLIND PACK.
# ------------------------------------------------------------

ALL_SEEDS=(
  "11"
  "37"
  "61"
  "101"
  "202"
  "303"
  "777"
  "1337"
  "424242"
  "8675309"
)

# ------------------------------------------------------------
# Only these actually require new synthesis.
#
# 202 / 303 / 424242 already exist with this exact reference
# and the complete reference-regression corpus.
# ------------------------------------------------------------

NEW_SEEDS=(
  "11"
  "37"
  "61"
  "101"
  "777"
  "1337"
  "8675309"
)

# ------------------------------------------------------------
# Existing benchmark runs used as immutable source artifacts.
#
# Override through environment variables if necessary.
# ------------------------------------------------------------

FINALISTS_RUN_ID="${FINALISTS_RUN_ID:-20260829T035529Z-35a384d7}"
AUDITORY_RUN_ID="${AUDITORY_RUN_ID:-20260829T020355Z-8cc499e1}"

FINALISTS_RUN="$REFERENCE_OUTPUT_ROOT/$FINALISTS_RUN_ID"
AUDITORY_RUN="$REFERENCE_OUTPUT_ROOT/$AUDITORY_RUN_ID"

EXPECTED_NEW_GENERATIONS="$((${#CASES[@]} * ${#NEW_SEEDS[@]}))"
EXPECTED_FINAL_EVALUATIONS="$((${#CASES[@]} * ${#ALL_SEEDS[@]}))"
EXPECTED_REUSED="$((EXPECTED_FINAL_EVALUATIONS - EXPECTED_NEW_GENERATIONS))"

CUSTOM_CORPUS="/tmp/neovids-seed-robustness-corpus-$$.yaml"

RUN=false
RESUME_ID=""

usage() {
  cat <<USAGE
Usage:

  $0
      Dry plan only. No Qwen synthesis.

  $0 --run
      Run the 42 new generations and build the 60-WAV blind pack.

  $0 --resume RUN_ID
      Resume an interrupted seed-robustness run and rebuild the blind pack.

Environment overrides:

  ORCH_DIR
  QWEN_DIR
  REFERENCES_DIR
  OUTPUT_ROOT
  REFERENCE_OUTPUT_ROOT
  EXPORT_DIR
  FINALISTS_RUN_ID
  AUDITORY_RUN_ID
  QWEN_READY_URL
USAGE
}

case "${1:-}" in
  "")
    ;;
  --run)
    RUN=true
    ;;
  --resume)
    if [[ -z "${2:-}" ]]; then
      echo "ERROR: --resume requires a RUN_ID."
      usage
      exit 2
    fi
    RUN=true
    RESUME_ID="$2"
    ;;
  -h|--help)
    usage
    exit 0
    ;;
  *)
    echo "ERROR: unknown argument: $1"
    usage
    exit 2
    ;;
esac

PLAN_LOG=""
RUN_LOG=""

cleanup() {
  if [[ -n "${PLAN_LOG:-}" && -f "$PLAN_LOG" ]]; then
    rm -f "$PLAN_LOG"
  fi

  if [[ -n "${RUN_LOG:-}" && -f "$RUN_LOG" ]]; then
    rm -f "$RUN_LOG"
  fi

  if [[ -d "$ORCH_DIR" ]]; then
    (
      cd "$ORCH_DIR"
      docker compose exec -T api rm -f "$CUSTOM_CORPUS" >/dev/null 2>&1 || true
    )
  fi
}

trap cleanup EXIT

echo
echo "============================================================"
echo " NeoVids - Seed Robustness Sweep"
echo "============================================================"
echo
echo "Reference:"
echo "  $REFERENCE"
echo
echo "Clone mode:"
echo "  $CLONE_MODE"
echo
echo "Generation profile:"
echo "  $GENERATION_PROFILE"
echo
echo "Synthesis language:"
echo "  $SYNTHESIS_LANGUAGE"
echo
echo "ASR language:"
echo "  $ASR_LANGUAGE"
echo
echo "Strategic cases:"
for case_id in "${CASES[@]}"; do
  echo "  - $case_id"
done
echo
echo "All evaluation seeds:"
for seed in "${ALL_SEEDS[@]}"; do
  echo "  - $seed"
done
echo
echo "New synthesis seeds:"
for seed in "${NEW_SEEDS[@]}"; do
  echo "  - $seed"
done
echo
echo "Historical seeds reused:"
echo "  - 202"
echo "  - 303"
echo "  - 424242"
echo
echo "New generations:"
echo "  $EXPECTED_NEW_GENERATIONS"
echo
echo "Historical WAVs reused:"
echo "  $EXPECTED_REUSED"
echo
echo "Final blind evaluation:"
echo "  $EXPECTED_FINAL_EVALUATIONS WAVs"
echo

# ============================================================
# Basic host / container preflight
# ============================================================

if [[ ! -d "$ORCH_DIR" ]]; then
  echo "ERROR: Orchestrator directory not found:"
  echo "  $ORCH_DIR"
  exit 1
fi

cd "$ORCH_DIR"

API_CONTAINER="$(docker compose ps -q api)"

if [[ -z "$API_CONTAINER" ]]; then
  echo "ERROR: Orchestrator API container is not running."
  echo
  echo "Check:"
  echo "  cd $ORCH_DIR"
  echo "  docker compose ps"
  exit 1
fi

echo "============================================================"
echo " PREFLIGHT"
echo "============================================================"
echo

echo "Checking winning reference..."

if docker compose exec -T api test -r "$REFERENCES_DIR/$REFERENCE"; then
  echo "  OK  $REFERENCE"
else
  echo "  ERROR  reference not found/readable:"
  echo "  $REFERENCES_DIR/$REFERENCE"
  exit 1
fi

echo
echo "Checking historical benchmark manifests..."

for path in "$FINALISTS_RUN" "$AUDITORY_RUN"; do
  if docker compose exec -T api test -r "$path/manifest.jsonl"; then
    echo "  OK  $path"
  else
    echo "  ERROR  historical manifest not found:"
    echo "  $path/manifest.jsonl"
    echo
    echo "If the run ID differs, override FINALISTS_RUN_ID or AUDITORY_RUN_ID."
    exit 1
  fi
done

# ============================================================
# Create a TEMPORARY corpus projection inside the API container.
#
# No repository file is changed.
#
# It copies the exact canonical case mappings from the existing
# reference_regression_corpus.yaml and selects only CASES[].
# ============================================================

echo
echo "Creating temporary six-case corpus projection..."

docker compose exec -T api \
  python - "$CUSTOM_CORPUS" "${CASES[@]}" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

import yaml


destination = Path(sys.argv[1])
wanted_ids = sys.argv[2:]

source_candidates = (
    Path("config/reference_regression_corpus.yaml"),
    Path("/app/config/reference_regression_corpus.yaml"),
)

source = next(
    (candidate for candidate in source_candidates if candidate.is_file()),
    None,
)

if source is None:
    raise SystemExit(
        "canonical reference_regression_corpus.yaml was not found inside the API container"
    )

raw = yaml.safe_load(source.read_text(encoding="utf-8"))

if not isinstance(raw, dict):
    raise SystemExit("canonical reference corpus is not a mapping")

quick = raw.get("quick_cases")
full = raw.get("full_cases")

if not isinstance(quick, list) or not isinstance(full, list):
    raise SystemExit("canonical corpus quick_cases/full_cases are invalid")

all_cases = quick + full

by_id = {}

for item in all_cases:
    if not isinstance(item, dict):
        raise SystemExit("canonical corpus contains a non-mapping case")

    case_id = str(item.get("id", "")).strip()

    if not case_id:
        raise SystemExit("canonical corpus contains a case without id")

    by_id[case_id] = item

missing = [case_id for case_id in wanted_ids if case_id not in by_id]

if missing:
    raise SystemExit(
        "requested seed robustness cases missing from canonical corpus: "
        + ", ".join(missing)
    )

selected = [by_id[case_id] for case_id in wanted_ids]

payload = {
    # Preserve the canonical schema and version because the selected
    # cases themselves are unchanged. This is only a projection.
    "schema": raw.get("schema"),
    "version": raw.get("version"),
    "quick_cases": selected,
    "full_cases": [],
}

destination.write_text(
    yaml.safe_dump(
        payload,
        allow_unicode=True,
        sort_keys=False,
        width=120,
    ),
    encoding="utf-8",
)

print(f"Canonical corpus: {source}")
print(f"Temporary corpus: {destination}")
print("Selected cases:")

for item in selected:
    print(f"  - {item['id']}")
PY

echo
echo "Temporary corpus created."

# ============================================================
# Verify that the three historical seeds really contain the
# required reference + all six cases + valid WAVs.
#
# This also protects us from accidentally mixing a different ref.
# ============================================================

echo
echo "Validating historical reusable WAVs..."

docker compose exec -T api \
  python - \
  "$FINALISTS_RUN" \
  "$AUDITORY_RUN" \
  "$REFERENCE" \
  "${CASES[@]}" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path


finalists = Path(sys.argv[1])
auditory = Path(sys.argv[2])
reference = sys.argv[3]
cases = tuple(sys.argv[4:])

requirements = {
    202: finalists,
    303: finalists,
    424242: auditory,
}


def valid_rows(run_dir: Path):
    manifest = run_dir / "manifest.jsonl"

    with manifest.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()

            if not line:
                continue

            row = json.loads(line)

            if row.get("reference") != reference:
                continue

            if row.get("generation_status") != "success":
                continue

            if row.get("artifact_integrity_status") != "valid":
                continue

            wav = row.get("wav")

            if not wav:
                continue

            wav_path = run_dir / wav

            if not wav_path.is_file():
                continue

            yield row


for seed, run_dir in requirements.items():
    rows = {}

    for row in valid_rows(run_dir):
        if row.get("requested_seed") != seed:
            continue

        case_id = row.get("case_id")

        if case_id in cases:
            rows[case_id] = row

    missing = [case_id for case_id in cases if case_id not in rows]

    if missing:
        raise SystemExit(
            f"historical seed {seed} is missing reusable cases in {run_dir}: "
            + ", ".join(missing)
        )

    print(
        f"  OK seed={seed} run={run_dir.name} "
        f"reference={reference} cases={len(rows)}"
    )

print("Historical reuse preflight: PASS")
PY

# ============================================================
# Optional reference metadata
# ============================================================

META_ARGS=()

if docker compose exec -T api test -f "$REFERENCES_DIR/reference_metadata.yaml"; then
  META_ARGS=(
    --reference-metadata
    "$REFERENCES_DIR/reference_metadata.yaml"
  )
fi

# ============================================================
# Wrapper around the existing production benchmark runner.
#
# The runner currently has no --cases selector.
#
# Instead of editing it or changing the repository corpus, this
# process changes ONLY the default argument of the imported corpus
# loader inside this one Python process.
#
# Repository files and persistent runtime state remain untouched.
# ============================================================

run_benchmark() {
  docker compose exec -T api \
    python - "$CUSTOM_CORPUS" "$@" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

import scripts.benchmark_voice_references as benchmark


custom_corpus = Path(sys.argv[1])

# benchmark_voice_references imported this same function object.
# Change its default path only for THIS Python process.
benchmark.load_reference_corpus.__defaults__ = (custom_corpus,)

raise SystemExit(
    benchmark.main(sys.argv[2:])
)
PY
}

BENCHMARK_ARGS=(
  --references-dir "$REFERENCES_DIR"
  --mode quick
  --refs "$REFERENCE"
  --generation "$GENERATION_PROFILE"
  --seeds "${NEW_SEEDS[@]}"
  --asr-language "$ASR_LANGUAGE"
  --output "$OUTPUT_ROOT"
  "${META_ARGS[@]}"
)

if [[ -n "$RESUME_ID" ]]; then
  BENCHMARK_ARGS+=(
    --resume "$RESUME_ID"
  )
fi

# ============================================================
# Dry plan
# ============================================================

echo
echo "============================================================"
echo " DRY PLAN"
echo "============================================================"
echo

PLAN_LOG="$(mktemp)"

run_benchmark "${BENCHMARK_ARGS[@]}" | tee "$PLAN_LOG"

if ! grep -q "Planned generations: $EXPECTED_NEW_GENERATIONS" "$PLAN_LOG"; then
  echo
  echo "ERROR: expected $EXPECTED_NEW_GENERATIONS planned generations."
  echo
  echo "Do NOT run the GPU benchmark until this is understood."
  exit 1
fi

if [[ "$RUN" != true ]]; then
  echo
  echo "============================================================"
  echo " PLAN COMPLETE"
  echo "============================================================"
  echo
  echo "Expected new matrix confirmed:"
  echo "  ${#NEW_SEEDS[@]} seeds x ${#CASES[@]} cases"
  echo "  = $EXPECTED_NEW_GENERATIONS new generations"
  echo
  echo "Final listening pack will contain:"
  echo "  ${#ALL_SEEDS[@]} seeds x ${#CASES[@]} cases"
  echo "  = $EXPECTED_FINAL_EVALUATIONS WAVs"
  echo
  echo "No synthesis was performed."
  echo
  echo "Run for real with:"
  echo
  echo "  ./run_seed_robustness.sh --run"
  echo
  exit 0
fi

# ============================================================
# Qwen readiness
# ============================================================

echo
echo "============================================================"
echo " QWEN READINESS"
echo "============================================================"
echo

READY_BODY="$(mktemp)"

HTTP_CODE="$(
  curl -sS \
    -o "$READY_BODY" \
    -w '%{http_code}' \
    "$QWEN_READY_URL" \
    || true
)"

if [[ "$HTTP_CODE" != "200" ]]; then
  echo "ERROR: Qwen is not ready. HTTP $HTTP_CODE"
  echo
  cat "$READY_BODY" || true
  echo

  if [[ -d "$QWEN_DIR" ]]; then
    echo "Recent Qwen logs:"
    (
      cd "$QWEN_DIR"
      docker compose logs --tail=100 qwen-tts || true
    )
  fi

  rm -f "$READY_BODY"
  exit 1
fi

rm -f "$READY_BODY"

echo "Qwen ready: HTTP 200"

# ============================================================
# Real GPU run
# ============================================================

echo
echo "============================================================"
echo " REAL GPU RUN"
echo "============================================================"
echo
echo "Generating only the seven previously untested seeds."
echo
echo "Known historical seeds 202 / 303 / 424242 will NOT be"
echo "regenerated."
echo

RUN_LOG="$(mktemp)"

set +e
run_benchmark "${BENCHMARK_ARGS[@]}" --run | tee "$RUN_LOG"
BENCHMARK_EXIT="${PIPESTATUS[0]}"
set -e

if [[ "$BENCHMARK_EXIT" -ne 0 ]]; then
  mkdir -p "$EXPORT_DIR"

  FAILURE_LOG="$EXPORT_DIR/seed-robustness-failure-$(date -u +%Y%m%dT%H%M%SZ).log"

  cp "$RUN_LOG" "$FAILURE_LOG" || true

  echo
  echo "ERROR: benchmark exited with code $BENCHMARK_EXIT"
  echo
  echo "Failure log copied to:"
  echo "  $FAILURE_LOG"
  echo
  echo "The underlying benchmark is resumable."
  exit "$BENCHMARK_EXIT"
fi

RUN_PATH="$(
  grep -oE 'output=[^ ]+' "$RUN_LOG" \
  | tail -1 \
  | cut -d= -f2-
)"

if [[ -z "$RUN_PATH" ]]; then
  if [[ -n "$RESUME_ID" ]]; then
    RUN_PATH="$OUTPUT_ROOT/$RESUME_ID"
  else
    echo "ERROR: could not determine benchmark run path."
    exit 1
  fi
fi

RUN_ID="$(basename "$RUN_PATH")"

echo
echo "New seed run:"
echo "  $RUN_ID"
echo
echo "Container path:"
echo "  $RUN_PATH"

# ============================================================
# Build combined BLIND SEED evaluation
#
# New run:
#   11 37 61 101 777 1337 8675309
#
# Reused:
#   202 303     <- finalist run
#   424242      <- previous auditory run
#
# Labels A-J hide the seed.
#
# One seed maps to the SAME blind label across all six cases.
# This allows human evaluation of consistency without knowing
# the numeric seed.
# ============================================================

echo
echo "============================================================"
echo " CREATING 60-WAV BLIND SEED PACK"
echo "============================================================"
echo

docker compose exec -T api \
  python - \
  "$RUN_PATH" \
  "$FINALISTS_RUN" \
  "$AUDITORY_RUN" \
  "$REFERENCE" \
  "${CASES[@]}" <<'PY'
from __future__ import annotations

import csv
import hashlib
import json
import os
import random
import shutil
import sys
from collections import defaultdict
from pathlib import Path


new_run = Path(sys.argv[1])
finalists_run = Path(sys.argv[2])
auditory_run = Path(sys.argv[3])
reference = sys.argv[4]
cases = tuple(sys.argv[5:])

desired_seeds = (
    11,
    37,
    61,
    101,
    202,
    303,
    777,
    1337,
    424242,
    8675309,
)

new_seeds = {
    11,
    37,
    61,
    101,
    777,
    1337,
    8675309,
}

source_for_seed = {
    202: finalists_run,
    303: finalists_run,
    424242: auditory_run,
}

for seed in new_seeds:
    source_for_seed[seed] = new_run

blind_dir = new_run / "blind_seed_sweep"
private_dir = blind_dir / "private"

if blind_dir.exists():
    shutil.rmtree(blind_dir)

blind_dir.mkdir(parents=True)
private_dir.mkdir(parents=True)


def read_manifest(run_dir: Path):
    manifest = run_dir / "manifest.jsonl"

    if not manifest.is_file():
        raise SystemExit(f"manifest missing: {manifest}")

    result = {}

    with manifest.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()

            if not line:
                continue

            row = json.loads(line)

            if row.get("reference") != reference:
                continue

            if row.get("generation_status") != "success":
                continue

            if row.get("artifact_integrity_status") != "valid":
                continue

            seed = row.get("requested_seed")
            case_id = row.get("case_id")

            if seed not in desired_seeds or case_id not in cases:
                continue

            wav = row.get("wav")

            if not wav:
                continue

            source = run_dir / wav

            if not source.is_file():
                continue

            # If a resume ever produced another valid record for the same cell,
            # the latest valid manifest entry wins.
            result[(int(seed), str(case_id))] = (row, source)

    return result


manifest_cache = {}

for run_dir in {new_run, finalists_run, auditory_run}:
    manifest_cache[run_dir] = read_manifest(run_dir)

entries = []

for seed in desired_seeds:
    run_dir = source_for_seed[seed]
    rows = manifest_cache[run_dir]

    for case_id in cases:
        key = (seed, case_id)

        if key not in rows:
            raise SystemExit(
                f"missing valid WAV for seed={seed} case={case_id} "
                f"in run={run_dir}"
            )

        row, source = rows[key]

        entries.append(
            {
                "seed": seed,
                "case": case_id,
                "row": row,
                "source": source,
                "source_run": run_dir,
            }
        )

expected = len(desired_seeds) * len(cases)

if len(entries) != expected:
    raise SystemExit(
        f"expected {expected} combined entries, got {len(entries)}"
    )

# ------------------------------------------------------------
# Cross-run invariants
# ------------------------------------------------------------

reference_hashes = {
    item["row"].get("reference_sha256")
    for item in entries
    if item["row"].get("reference_sha256")
}

if len(reference_hashes) != 1:
    raise SystemExit(
        "combined seed evaluation does not use exactly one reference SHA-256"
    )

clone_modes = {
    item["row"].get("clone_mode")
    for item in entries
    if item["row"].get("clone_mode")
}

if clone_modes != {"x_vector_only"}:
    raise SystemExit(
        f"unexpected clone mode set in combined evaluation: {clone_modes}"
    )

generation_profiles = {
    item["row"].get("generation_profile")
    for item in entries
    if item["row"].get("generation_profile")
}

if generation_profiles and generation_profiles != {"narration_v1"}:
    raise SystemExit(
        "combined evaluation contains more than one generation profile: "
        f"{generation_profiles}"
    )

# Every case must contain exactly the same canonical text regardless of seed.
for case_id in cases:
    texts = {
        item["row"].get("canonical_text")
        for item in entries
        if item["case"] == case_id
    }

    if len(texts) != 1:
        raise SystemExit(
            f"canonical text differs across seeds for case={case_id}"
        )

# Compare pronunciation/lexicon identities where the manifest exposes one.
lexicon_keys = (
    "pronunciation_lexicon_identity",
    "lexicon_identity",
)

lexicon_values = set()

for item in entries:
    row = item["row"]

    for key in lexicon_keys:
        value = row.get(key)

        if value:
            lexicon_values.add(str(value))
            break

if len(lexicon_values) > 1:
    raise SystemExit(
        "pronunciation/lexicon identity differs across combined runs: "
        + ", ".join(sorted(lexicon_values))
    )

# ------------------------------------------------------------
# Blind seed mapping
# ------------------------------------------------------------

labels = [
    chr(ord("A") + index)
    for index in range(len(desired_seeds))
]

mapping_seed = int.from_bytes(
    hashlib.sha256(
        (
            new_run.name
            + "|seed-robustness-blind-v1"
        ).encode("utf-8")
    ).digest()[:8],
    "big",
)

rng = random.Random(mapping_seed)
rng.shuffle(labels)

seed_to_label = dict(
    zip(desired_seeds, labels, strict=True)
)

# ------------------------------------------------------------
# Private mapping
# ------------------------------------------------------------

with (private_dir / "seed_map_PRIVATE.csv").open(
    "w",
    newline="",
    encoding="utf-8",
) as handle:
    writer = csv.writer(handle)

    writer.writerow(
        [
            "blind_seed",
            "seed",
            "source_run_id",
        ]
    )

    for seed in desired_seeds:
        writer.writerow(
            [
                seed_to_label[seed],
                seed,
                source_for_seed[seed].name,
            ]
        )

# ------------------------------------------------------------
# Copy/hardlink the WAVs into a standalone blind pack.
# ------------------------------------------------------------

listening_rows = []
private_manifest_rows = []

for item in entries:
    seed = item["seed"]
    case_id = item["case"]
    row = item["row"]
    source = item["source"]

    label = seed_to_label[seed]

    destination_dir = blind_dir / case_id
    destination_dir.mkdir(parents=True, exist_ok=True)

    destination = destination_dir / f"{label}.wav"

    if destination.exists():
        destination.unlink()

    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)

    listening_rows.append(
        {
            "case": case_id,
            "blind_seed": label,
            "wav": str(destination.relative_to(new_run)),
            "canonical_text": row.get("canonical_text", ""),
            "speaker_identity_stability_1_5": "",
            "timbre_similarity_1_5": "",
            "naturalness_1_5": "",
            "pace_1_5": "",
            "rhythm_prosody_1_5": "",
            "ptbr_accent_1_5": "",
            "pronunciation_1_5": "",
            "breath_artifacts_1_5": "",
            "click_artifacts_1_5": "",
            "overall_1_5": "",
            "catastrophic_failure": "",
            "failure_type": "",
            "notes": "",
        }
    )

    private_row = dict(row)
    private_row["_seed_robustness_source_run_id"] = item["source_run"].name
    private_row["_blind_seed"] = label

    private_manifest_rows.append(private_row)

# ------------------------------------------------------------
# Randomized play order within each case.
# ------------------------------------------------------------

groups = defaultdict(list)

for row in listening_rows:
    groups[row["case"]].append(row)

ordered = []

for case_id in cases:
    group = groups[case_id]

    local_seed = int.from_bytes(
        hashlib.sha256(
            (
                new_run.name
                + "|"
                + case_id
                + "|seed-play-order-v1"
            ).encode("utf-8")
        ).digest()[:8],
        "big",
    )

    local_rng = random.Random(local_seed)
    local_rng.shuffle(group)

    for index, row in enumerate(group, start=1):
        row["play_order"] = index
        ordered.append(row)

fields = [
    "case",
    "play_order",
    "blind_seed",
    "wav",
    "canonical_text",
    "speaker_identity_stability_1_5",
    "timbre_similarity_1_5",
    "naturalness_1_5",
    "pace_1_5",
    "rhythm_prosody_1_5",
    "ptbr_accent_1_5",
    "pronunciation_1_5",
    "breath_artifacts_1_5",
    "click_artifacts_1_5",
    "overall_1_5",
    "catastrophic_failure",
    "failure_type",
    "notes",
]

with (blind_dir / "listening.csv").open(
    "w",
    newline="",
    encoding="utf-8",
) as handle:
    writer = csv.DictWriter(
        handle,
        fieldnames=fields,
    )

    writer.writeheader()
    writer.writerows(ordered)

with (private_dir / "combined_manifest_PRIVATE.jsonl").open(
    "w",
    encoding="utf-8",
) as handle:
    for row in private_manifest_rows:
        handle.write(
            json.dumps(
                row,
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
        )

# ------------------------------------------------------------
# Human review guide
# ------------------------------------------------------------

(blind_dir / "README.txt").write_text(
    """NeoVids - Seed Robustness Blind Evaluation

REFERENCE IS FIXED:
ref_full_processed_raw.wav

CLONE MODE:
x_vector_only

GENERATION PROFILE:
narration_v1

This evaluation compares TEN seeds across SIX strategic cases.

DO NOT OPEN:

private/seed_map_PRIVATE.csv
private/combined_manifest_PRIVATE.jsonl

until listening is complete.

A-J represent seeds.

The same blind letter represents the same seed across all cases.

One of the seeds intentionally remains a historically known
speaker-drift canary. Do not attempt to infer which label it is.

The goal is NOT to find the WAV with the single best moment.

The goal is to find seeds whose WORST behavior remains production-safe.

============================================================
IMMEDIATE ELIMINATION CONDITIONS
============================================================

Treat as catastrophic_failure=yes when appropriate:

- clearly wrong speaker
- female/wrong-gender voice trajectory
- severe unexpected PT-PT speaker/accent drift
- speaker identity changes inside the WAV
- runaway generation
- obvious missing content
- severe repetition
- major corrupted/invalid speech

Suggested failure_type examples:

wrong_speaker
speaker_drift
dialect_drift
runaway
missing_content
severe_repetition
other

============================================================
SCORING
============================================================

All 1-5 fields use:

5 = excellent
4 = good
3 = acceptable
2 = poor
1 = very poor

speaker_identity_stability_1_5

5 = expected speaker throughout
4 = tiny harmless drift
3 = noticeable but acceptable
2 = major drift
1 = clearly wrong speaker/voice

pace_1_5

5 = natural production-ready pace
4 = slightly slow/fast but good
3 = noticeable pacing problem
2 = distracting
1 = unusable

naturalness_1_5

Judge robotic delivery separately from pure pacing.

breath_artifacts_1_5 / click_artifacts_1_5

5 = no problematic artifact
4 = barely perceptible
3 = noticeable but acceptable
2 = distracting
1 = severe

============================================================
PRIORITY
============================================================

1. no catastrophic speaker/content failures
2. speaker identity stability
3. naturalness
4. rhythm/prosody
5. pace
6. PT-BR accent
7. pronunciation
8. timbre similarity
9. breathing/click artifacts

Do not choose based on loudness.

The preferred production seed should be ROBUST, not merely capable
of producing one exceptional sample.
""",
    encoding="utf-8",
)

# ------------------------------------------------------------
# Small public metadata file that does NOT reveal seed mapping.
# ------------------------------------------------------------

public_metadata = {
    "schema": "neovids_seed_robustness_blind_v1",
    "reference": reference,
    "reference_sha256": next(iter(reference_hashes)),
    "clone_mode": "x_vector_only",
    "generation_profile": "narration_v1",
    "case_count": len(cases),
    "blind_seed_count": len(desired_seeds),
    "evaluation_count": len(entries),
    "cases": list(cases),
    "historical_artifacts_reused": True,
}

(blind_dir / "metadata.json").write_text(
    json.dumps(
        public_metadata,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)

print(f"Reference:          {reference}")
print(f"Cases:              {len(cases)}")
print(f"Blind seeds:        {len(desired_seeds)}")
print(f"Total WAVs:         {len(entries)}")
print(f"Listening sheet:    {blind_dir / 'listening.csv'}")
print(f"Private seed map:   {private_dir / 'seed_map_PRIVATE.csv'}")
print("Combined blind pack: PASS")
PY

# ============================================================
# Export
# ============================================================

echo
echo "============================================================"
echo " EXPORTING"
echo "============================================================"
echo

mkdir -p "$EXPORT_DIR"

ARCHIVE="${RUN_ID}-seed-robustness.tar.gz"

docker compose exec -T api sh -lc "
  cd '$(dirname "$RUN_PATH")' &&
  tar -czf '/tmp/$ARCHIVE' '$RUN_ID'
"

docker cp \
  "$API_CONTAINER:/tmp/$ARCHIVE" \
  "$EXPORT_DIR/$ARCHIVE"

docker compose exec -T api rm -f "/tmp/$ARCHIVE" || true

echo
echo "============================================================"
echo " COMPLETE"
echo "============================================================"
echo
echo "Reference frozen:"
echo "  $REFERENCE"
echo
echo "New synthesis:"
echo "  ${#NEW_SEEDS[@]} seeds x ${#CASES[@]} cases"
echo "  = $EXPECTED_NEW_GENERATIONS WAVs"
echo
echo "Historical reuse:"
echo "  3 seeds x ${#CASES[@]} cases"
echo "  = $EXPECTED_REUSED WAVs"
echo
echo "Final blind evaluation:"
echo "  ${#ALL_SEEDS[@]} seeds x ${#CASES[@]} cases"
echo "  = $EXPECTED_FINAL_EVALUATIONS WAVs"
echo
echo "Run ID:"
echo "  $RUN_ID"
echo
echo "Host archive:"
echo "  $EXPORT_DIR/$ARCHIVE"
echo
echo "Blind listening sheet:"
echo "  $RUN_PATH/blind_seed_sweep/listening.csv"
echo
echo "DO NOT OPEN UNTIL LISTENING IS FINISHED:"
echo "  $RUN_PATH/blind_seed_sweep/private/seed_map_PRIVATE.csv"
echo
echo "To extract the exported archive later:"
echo
echo "  cd $EXPORT_DIR"
echo "  mkdir -p ${RUN_ID}-seed-evaluation"
echo "  tar -xzf $ARCHIVE -C ${RUN_ID}-seed-evaluation"
echo
