#!/usr/bin/env bash
set -Eeuo pipefail

# ============================================================
# NeoVids - Selected Reference Auditory Evaluation
# ============================================================
#
# Stage:
#   Reference shortlist -> deeper human listening
#
# Matrix:
#   6 references
#   x 12 full-corpus cases
#   x 1 fresh seed
#   = 72 generations
#
# This is NOT the final 3-seed regression.
#
# Usage:
#
#   PLAN ONLY:
#     ./run_reference_auditory_round.sh
#
#   REAL GPU RUN:
#     ./run_reference_auditory_round.sh --run
#
# Optional:
#
#   AUDIT_SEED=123456 ./run_reference_auditory_round.sh --run
#
# ============================================================

ORCH_DIR="${ORCH_DIR:-$HOME/workspaces/neovids/orchestrator}"
QWEN_DIR="${QWEN_DIR:-$HOME/workspaces/neovids/qwen3-tts-workshop}"

REFERENCES_DIR="${REFERENCES_DIR:-/data/references}"
OUTPUT_ROOT="${OUTPUT_ROOT:-/data/audio/benchmarks/reference-regression}"

EXPORT_DIR="${EXPORT_DIR:-$ORCH_DIR/benchmark_exports}"

# Fresh fixed seed, intentionally different from the previous quick seed 101.
AUDIT_SEED="${AUDIT_SEED:-424242}"

QWEN_READY_URL="${QWEN_READY_URL:-http://localhost:8889/health/ready}"

REFS=(
  "ref.wav"
  "ref_1_pro_raw.wav"
  "ref_full_processed_raw.wav"
  "enhanced_ref.wav"
  "enhanced_ref_full_processed_raw.wav"
  "ref_older.wav"
)

RUN=false

case "${1:-}" in
  "")
    ;;
  --run)
    RUN=true
    ;;
  *)
    echo "Usage:"
    echo "  $0"
    echo "  $0 --run"
    exit 2
    ;;
esac

echo
echo "============================================================"
echo " NeoVids - Reference Auditory Round"
echo "============================================================"
echo
echo "References:"
for ref in "${REFS[@]}"; do
  echo "  - $ref"
done

echo
echo "Mode:              full corpus"
echo "Clone mode:        x_vector_only"
echo "Generation:        narration_v1"
echo "Synthesis lang:    Portuguese"
echo "ASR lang:          pt"
echo "Seed:              $AUDIT_SEED"
echo "References:        ${#REFS[@]}"
echo "Corpus cases:      12"
echo "Expected matrix:   $((${#REFS[@]} * 12)) generations"
echo

if [[ ! -d "$ORCH_DIR" ]]; then
  echo "ERROR: Orchestrator directory not found:"
  echo "  $ORCH_DIR"
  exit 1
fi

cd "$ORCH_DIR"

# ------------------------------------------------------------
# Check Orchestrator API container
# ------------------------------------------------------------

API_CONTAINER="$(docker compose ps -q api)"

if [[ -z "$API_CONTAINER" ]]; then
  echo "ERROR: Orchestrator API container is not running."
  echo
  echo "Start it first with:"
  echo "  cd $ORCH_DIR"
  echo "  docker compose up -d api"
  exit 1
fi

# ------------------------------------------------------------
# Validate selected references from the container perspective
# ------------------------------------------------------------

echo "Checking selected references inside Orchestrator..."

MISSING=0

for ref in "${REFS[@]}"; do
  if docker compose exec -T api test -r "$REFERENCES_DIR/$ref"; then
    echo "  OK  $ref"
  else
    echo "  MISSING  $ref"
    MISSING=1
  fi
done

if [[ "$MISSING" -ne 0 ]]; then
  echo
  echo "ERROR: one or more selected references are unavailable."
  exit 1
fi

# ------------------------------------------------------------
# Optional metadata
# ------------------------------------------------------------

META_ARGS=()

if docker compose exec -T api test -f "$REFERENCES_DIR/reference_metadata.yaml"; then
  echo
  echo "Using optional reference metadata:"
  echo "  $REFERENCES_DIR/reference_metadata.yaml"

  META_ARGS=(
    --reference-metadata
    "$REFERENCES_DIR/reference_metadata.yaml"
  )
else
  echo
  echo "No reference_metadata.yaml found."
  echo "Continuing without family/variant metadata."
fi

# ------------------------------------------------------------
# Benchmark base command
# ------------------------------------------------------------

BENCHMARK_CMD=(
  docker compose exec -T api
  python scripts/benchmark_voice_references.py
  --references-dir "$REFERENCES_DIR"
  --mode full
  --refs "${REFS[@]}"
  --seeds "$AUDIT_SEED"
  --output "$OUTPUT_ROOT"
  "${META_ARGS[@]}"
)

# ------------------------------------------------------------
# Always show the dry plan first
# ------------------------------------------------------------

echo
echo "============================================================"
echo " DRY PLAN"
echo "============================================================"
echo

"${BENCHMARK_CMD[@]}"

if [[ "$RUN" != true ]]; then
  echo
  echo "============================================================"
  echo " PLAN COMPLETE"
  echo "============================================================"
  echo
  echo "No synthesis was performed."
  echo
  echo "To execute the real GPU benchmark:"
  echo
  echo "  $0 --run"
  echo
  exit 0
fi

# ------------------------------------------------------------
# Check Qwen readiness before spending benchmark time
# ------------------------------------------------------------

echo
echo "============================================================"
echo " QWEN READINESS"
echo "============================================================"
echo

HTTP_CODE="$(
  curl -sS \
    -o /tmp/neovids-qwen-ready-body.$$ \
    -w '%{http_code}' \
    "$QWEN_READY_URL" \
    || true
)"

if [[ "$HTTP_CODE" != "200" ]]; then
  echo "ERROR: Qwen is not ready."
  echo "HTTP: $HTTP_CODE"
  echo

  if [[ -f /tmp/neovids-qwen-ready-body.$$ ]]; then
    cat /tmp/neovids-qwen-ready-body.$$
    echo
  fi

  rm -f /tmp/neovids-qwen-ready-body.$$

  echo
  echo "Recent Qwen logs:"
  echo

  if [[ -d "$QWEN_DIR" ]]; then
    (
      cd "$QWEN_DIR"
      docker compose logs --tail=100 qwen-tts || true
    )
  fi

  exit 1
fi

rm -f /tmp/neovids-qwen-ready-body.$$

echo "Qwen ready: HTTP 200"

# ------------------------------------------------------------
# Real benchmark
# ------------------------------------------------------------

echo
echo "============================================================"
echo " REAL GPU RUN"
echo "============================================================"
echo

RUN_LOG="$(mktemp)"

set +e

"${BENCHMARK_CMD[@]}" --run | tee "$RUN_LOG"

BENCHMARK_EXIT="${PIPESTATUS[0]}"

set -e

if [[ "$BENCHMARK_EXIT" -ne 0 ]]; then
  echo
  echo "ERROR: benchmark exited with code $BENCHMARK_EXIT"
  echo
  echo "Log preserved at:"
  echo "  $RUN_LOG"
  exit "$BENCHMARK_EXIT"
fi

# Expected final line contains:
# output=/data/audio/benchmarks/reference-regression/<run_id>

RUN_PATH="$(
  grep -oE 'output=[^ ]+' "$RUN_LOG" \
    | tail -1 \
    | cut -d= -f2-
)"

rm -f "$RUN_LOG"

if [[ -z "$RUN_PATH" ]]; then
  echo
  echo "ERROR: benchmark completed but run path could not be parsed."
  exit 1
fi

RUN_ID="$(basename "$RUN_PATH")"

echo
echo "Benchmark run:"
echo "  $RUN_ID"
echo
echo "Container path:"
echo "  $RUN_PATH"

# ------------------------------------------------------------
# Generate a BLIND listening package
# ------------------------------------------------------------
#
# We keep the original benchmark artifacts unchanged.
#
# The blind directory contains aliases A/B/C/D/E/F instead of
# reference filenames.
#
# Mapping is stored separately in:
#
#   blind/map_PRIVATE.csv
#
# Do not open that mapping until subjective listening is done.
#
# Hardlinks are used where possible to avoid duplicating audio.
# ------------------------------------------------------------

echo
echo "============================================================"
echo " CREATING BLIND LISTENING PACK"
echo "============================================================"
echo

docker compose exec -T api python - "$RUN_PATH" <<'PY'
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

run_dir = Path(sys.argv[1])
manifest_path = run_dir / "manifest.jsonl"
blind_dir = run_dir / "blind"

if not manifest_path.is_file():
    raise SystemExit(f"manifest not found: {manifest_path}")

rows = []

with manifest_path.open("r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue

        item = json.loads(line)

        if item.get("generation_status") != "success":
            continue

        if item.get("artifact_integrity_status") != "valid":
            continue

        wav = item.get("wav")

        if not wav:
            continue

        source = run_dir / wav

        if not source.is_file():
            continue

        rows.append(item)

if not rows:
    raise SystemExit("no successful valid WAVs found")

references = sorted({row["reference"] for row in rows})

if len(references) > 26:
    labels = [f"R{i:02d}" for i in range(1, len(references) + 1)]
else:
    labels = [chr(ord("A") + i) for i in range(len(references))]

# Stable but opaque mapping for this run.
seed_material = (
    run_dir.name
    + "|neovids-blind-reference-audit-v1"
).encode("utf-8")

blind_seed = int.from_bytes(
    hashlib.sha256(seed_material).digest()[:8],
    "big",
)

rng = random.Random(blind_seed)
rng.shuffle(labels)

reference_to_label = dict(zip(references, labels, strict=True))

blind_dir.mkdir(parents=True, exist_ok=True)

# ------------------------------------------------------------
# Private mapping
# ------------------------------------------------------------

mapping_path = blind_dir / "map_PRIVATE.csv"

with mapping_path.open("w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)

    writer.writerow([
        "blind_reference",
        "reference",
        "reference_sha256",
        "reference_identity",
    ])

    first_by_ref = {}

    for row in rows:
        first_by_ref.setdefault(row["reference"], row)

    for reference in sorted(references):
        row = first_by_ref[reference]

        writer.writerow([
            reference_to_label[reference],
            reference,
            row.get("reference_sha256", ""),
            row.get("reference_identity", ""),
        ])

# ------------------------------------------------------------
# Create blind WAV aliases
# ------------------------------------------------------------

listening_rows = []

for row in rows:
    label = reference_to_label[row["reference"]]
    case_id = row["case_id"]
    seed = row["requested_seed"]

    source = run_dir / row["wav"]

    destination_dir = (
        blind_dir
        / case_id
        / f"seed_{seed}"
    )

    destination_dir.mkdir(parents=True, exist_ok=True)

    destination = destination_dir / f"{label}.wav"

    if destination.exists():
        destination.unlink()

    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)

    listening_rows.append({
        "case": case_id,
        "seed": seed,
        "blind_reference": label,
        "wav": str(destination.relative_to(run_dir)),
        "canonical_text": row.get("canonical_text", ""),
        "timbre_similarity_1_5": "",
        "naturalness_1_5": "",
        "rhythm_prosody_1_5": "",
        "ptbr_accent_1_5": "",
        "pronunciation_1_5": "",
        "breath_artifacts_1_5": "",
        "click_artifacts_1_5": "",
        "overall_1_5": "",
        "preferred": "",
        "notes": "",
    })

# Randomize playback order WITHIN each case/seed.
groups = defaultdict(list)

for row in listening_rows:
    groups[(row["case"], row["seed"])].append(row)

ordered = []

for group_key in sorted(groups):
    group = groups[group_key]

    local_seed = int.from_bytes(
        hashlib.sha256(
            (
                run_dir.name
                + "|"
                + str(group_key)
                + "|blind-order-v1"
            ).encode("utf-8")
        ).digest()[:8],
        "big",
    )

    local_rng = random.Random(local_seed)
    local_rng.shuffle(group)

    for index, row in enumerate(group, start=1):
        row["play_order"] = index
        ordered.append(row)

# ------------------------------------------------------------
# Blind listening worksheet
# ------------------------------------------------------------

listening_path = blind_dir / "listening.csv"

fields = [
    "case",
    "seed",
    "play_order",
    "blind_reference",
    "wav",
    "canonical_text",
    "timbre_similarity_1_5",
    "naturalness_1_5",
    "rhythm_prosody_1_5",
    "ptbr_accent_1_5",
    "pronunciation_1_5",
    "breath_artifacts_1_5",
    "click_artifacts_1_5",
    "overall_1_5",
    "preferred",
    "notes",
]

with listening_path.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fields)
    writer.writeheader()
    writer.writerows(ordered)

# ------------------------------------------------------------
# Human instructions
# ------------------------------------------------------------

readme = blind_dir / "README.txt"

readme.write_text(
    """NeoVids - Blind Reference Listening Pack

IMPORTANT

Do NOT open map_PRIVATE.csv before completing subjective listening.

Each reference has been replaced by an opaque label such as:

A
B
C
D
E
F

The mapping remains stable across all cases in this run.

For each case, use listening.csv play_order to avoid a fixed presentation order.

Recommended scoring:

5 = excellent
4 = good
3 = acceptable
2 = poor
1 = very poor

For breath_artifacts and click_artifacts:

5 = no problematic artifact
4 = minor / barely perceptible
3 = noticeable but acceptable
2 = distracting
1 = severe

Focus primarily on:

- timbre similarity;
- naturalness;
- rhythm/prosody;
- Brazilian Portuguese accent;
- pronunciation;
- artificial breathing;
- clicks/transients;
- overall production suitability.

Do not choose based only on loudness.

The original benchmark WAVs remain unchanged.

After scoring is complete, map_PRIVATE.csv may be opened to reveal
which blind label corresponds to which source reference.
""",
    encoding="utf-8",
)

print(f"Blind references: {len(references)}")
print(f"Blind WAVs:       {len(listening_rows)}")
print(f"Listening CSV:    {listening_path}")
print(f"Private mapping:  {mapping_path}")
PY

# ------------------------------------------------------------
# Archive complete run to host
# ------------------------------------------------------------

echo
echo "============================================================"
echo " EXPORTING RUN"
echo "============================================================"
echo

mkdir -p "$EXPORT_DIR"

ARCHIVE_NAME="${RUN_ID}-auditory.tar.gz"

docker compose exec -T api sh -lc "
  cd '$(dirname "$RUN_PATH")' &&
  tar -czf '/tmp/$ARCHIVE_NAME' '$RUN_ID'
"

docker cp \
  "$API_CONTAINER:/tmp/$ARCHIVE_NAME" \
  "$EXPORT_DIR/$ARCHIVE_NAME"

docker compose exec -T api rm -f "/tmp/$ARCHIVE_NAME" || true

echo
echo "============================================================"
echo " COMPLETE"
echo "============================================================"
echo
echo "Run:"
echo "  $RUN_ID"
echo
echo "Container artifacts:"
echo "  $RUN_PATH"
echo
echo "Blind listening:"
echo "  $RUN_PATH/blind/"
echo
echo "Host archive:"
echo "  $EXPORT_DIR/$ARCHIVE_NAME"
echo
echo "Expected outputs:"
echo "  6 references x 12 cases x 1 seed = 72 WAVs"
echo
echo "IMPORTANT:"
echo "  Listen using blind/listening.csv first."
echo "  Do NOT open blind/map_PRIVATE.csv until scoring is finished."
echo
