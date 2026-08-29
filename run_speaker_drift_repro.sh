#!/usr/bin/env bash
set -Eeuo pipefail

# ============================================================
# NeoVids - Speaker Drift Reproducibility Benchmark
#
# Objective:
#
# Determine whether the observed wrong-speaker / PT-PT / female
# voice drift is:
#
#   - reproducible for a specific text + seed;
#   - intermittent across independent jobs;
#   - specific to long-form multi-chunk synthesis;
#   - reproducible when the exact first long-form chunk is
#     synthesized in isolation.
#
# IMPORTANT:
#
# This benchmark DOES NOT tune:
#
#   - reference;
#   - temperature;
#   - top_p;
#   - top_k;
#   - repetition penalty;
#   - chunking;
#   - pronunciation;
#   - post-processing.
#
# The generation variables stay frozen.
#
# Each repetition receives its own Idempotency-Key, while all
# actual synthesis inputs remain identical.
#
# The benchmark is resumable: repeating the SAME RUN_ID uses
# the same per-cell Idempotency-Key and therefore reattaches to
# the same Qwen job rather than burning another generation.
# ============================================================


# ------------------------------------------------------------
# Paths / services
# ------------------------------------------------------------

ORCH_DIR="${ORCH_DIR:-$HOME/workspaces/neovids/orchestrator}"
QWEN_DIR="${QWEN_DIR:-$HOME/workspaces/neovids/qwen3-tts-workshop}"

REFERENCES_DIR="${REFERENCES_DIR:-/data/references}"

OUTPUT_ROOT="${OUTPUT_ROOT:-/data/audio/benchmarks/speaker-drift-repro}"
EXPORT_DIR="${EXPORT_DIR:-$ORCH_DIR/benchmark_exports}"

QWEN_READY_URL="${QWEN_READY_URL:-http://localhost:8889/health/ready}"

REFERENCE="ref_full_processed_raw.wav"

GENERATION_PROFILE="narration_v1"
SYNTHESIS_LANGUAGE="Portuguese"
CLONE_MODE="x_vector_only"


# ------------------------------------------------------------
# Long-form seed field
#
# 61:
#   current human favorite / expected stable control
#
# 101:
#   conservative stable candidate
#
# 1337:
#   high-potential candidate with observed long-form drift
#
# 303:
#   strong historical seed
#
# 424242:
#   strong historical seed
#
# 8675309:
#   observed long-form drift
#
# 202:
#   historical positive drift canary
# ------------------------------------------------------------

LONG_FORM_SEEDS=(
  61
  101
  1337
  303
  424242
  8675309
  202
)


# ------------------------------------------------------------
# Isolated first-chunk test
#
# 61 = expected-good control
# 1337 / 8675309 = observed long-form drift cases
# ------------------------------------------------------------

ISOLATED_SEEDS=(
  61
  1337
  8675309
)

REPETITIONS=3

EXPECTED_LONG_FORM="$((${#LONG_FORM_SEEDS[@]} * REPETITIONS))"
EXPECTED_ISOLATED="$((${#ISOLATED_SEEDS[@]} * REPETITIONS))"
EXPECTED_TOTAL="$((EXPECTED_LONG_FORM + EXPECTED_ISOLATED))"


# ------------------------------------------------------------
# CLI
# ------------------------------------------------------------

RUN=false
RESTART_QWEN=false
RESUME_ID=""

usage() {
  cat <<USAGE
Usage:

  $0
      Dry plan only. No synthesis.

  $0 --run
      Execute the benchmark using the current Qwen process.

  $0 --run --restart-qwen
      Restart Qwen ONCE before the benchmark, wait for readiness,
      then execute all repetitions in one uninterrupted service
      session.

  $0 --resume RUN_ID
      Resume an interrupted benchmark without creating duplicate
      jobs for completed/in-flight cells.

  $0 --resume RUN_ID --restart-qwen
      Resume after first restarting Qwen.

Examples:

  ./run_speaker_drift_repro.sh

  ./run_speaker_drift_repro.sh --run --restart-qwen

  ./run_speaker_drift_repro.sh \
      --resume 20260829T170000Z-ab12cd34
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in

    --run)
      RUN=true
      shift
      ;;

    --restart-qwen)
      RESTART_QWEN=true
      shift
      ;;

    --resume)
      if [[ -z "${2:-}" ]]; then
        echo "ERROR: --resume requires RUN_ID"
        exit 2
      fi

      RUN=true
      RESUME_ID="$2"
      shift 2
      ;;

    -h|--help)
      usage
      exit 0
      ;;

    *)
      echo "ERROR: unknown argument: $1"
      echo
      usage
      exit 2
      ;;

  esac
done


# ------------------------------------------------------------
# Basic preflight
# ------------------------------------------------------------

if [[ ! -d "$ORCH_DIR" ]]; then
  echo "ERROR: Orchestrator directory not found:"
  echo "  $ORCH_DIR"
  exit 1
fi

if [[ ! -d "$QWEN_DIR" ]]; then
  echo "ERROR: Qwen directory not found:"
  echo "  $QWEN_DIR"
  exit 1
fi

cd "$ORCH_DIR"

API_CONTAINER="$(docker compose ps -q api)"

if [[ -z "$API_CONTAINER" ]]; then
  echo "ERROR: Orchestrator API is not running."
  exit 1
fi

if ! docker compose exec -T api \
    test -r "$REFERENCES_DIR/$REFERENCE"; then
  echo "ERROR: reference unavailable inside Orchestrator API:"
  echo "  $REFERENCES_DIR/$REFERENCE"
  exit 1
fi


# ============================================================
# Resolve the EXACT current long_form pipeline text.
#
# We intentionally reuse:
#
# canonical corpus text
#       ↓
# PortugueseTextNormalizer
#       ↓
# PronunciationPreprocessor
#
# This matches the normal reference regression path.
# ============================================================

echo
echo "============================================================"
echo " PREPARING CANONICAL LONG-FORM INPUT"
echo "============================================================"
echo

mapfile -t TEXT_INFO < <(
  docker compose exec -T api python - <<'PY'
from __future__ import annotations

import base64
from pathlib import Path

import yaml

from app.core.config import Settings
from app.services.pronunciation import PronunciationPreprocessor
from app.services.text_normalization import PortugueseTextNormalizer


settings = Settings(_env_file=None)

corpus_candidates = (
    Path("config/reference_regression_corpus.yaml"),
    Path("/app/config/reference_regression_corpus.yaml"),
)

corpus_path = next(
    (path for path in corpus_candidates if path.is_file()),
    None,
)

if corpus_path is None:
    raise SystemExit(
        "reference_regression_corpus.yaml was not found"
    )

raw = yaml.safe_load(
    corpus_path.read_text(encoding="utf-8")
)

cases = (
    list(raw.get("quick_cases") or [])
    + list(raw.get("full_cases") or [])
)

long_form = next(
    (
        item
        for item in cases
        if item.get("id") == "long_form"
    ),
    None,
)

if not long_form:
    raise SystemExit(
        "canonical long_form case was not found"
    )

canonical = str(long_form["text"])

normalizer = PortugueseTextNormalizer(
    version=settings.text_normalizer_version
)

normalized = normalizer.normalize(canonical)

preprocessor = PronunciationPreprocessor.load(
    settings.pronunciation_glossary_path
)

prepared = preprocessor.preprocess(
    normalized.normalized_text,
    canonical_text=canonical,
)


def b64(value: str) -> str:
    return base64.b64encode(
        value.encode("utf-8")
    ).decode("ascii")


print(b64(canonical))
print(b64(normalized.normalized_text))
print(b64(prepared.synthesis_text))

print(normalized.version)
print(normalized.identity)
print(prepared.glossary_identity)
PY
)

if [[ "${#TEXT_INFO[@]}" -ne 6 ]]; then
  echo "ERROR: failed to resolve long-form preparation."
  exit 1
fi

CANONICAL_B64="${TEXT_INFO[0]}"
NORMALIZED_B64="${TEXT_INFO[1]}"
SYNTHESIS_B64="${TEXT_INFO[2]}"

NORMALIZER_VERSION="${TEXT_INFO[3]}"
NORMALIZER_IDENTITY="${TEXT_INFO[4]}"
LEXICON_IDENTITY="${TEXT_INFO[5]}"

echo "Normalizer version:"
echo "  $NORMALIZER_VERSION"

echo
echo "Normalizer identity:"
echo "  $NORMALIZER_IDENTITY"

echo
echo "Pronunciation lexicon identity:"
echo "  $LEXICON_IDENTITY"


# ============================================================
# Resolve the EXACT first Qwen chunk using CURRENT Qwen runtime
# chunking configuration.
#
# This avoids hardcoding 250/400 in the experiment.
# ============================================================

echo
echo "============================================================"
echo " RESOLVING CURRENT QWEN FIRST CHUNK"
echo "============================================================"
echo

cd "$QWEN_DIR"

QWEN_CONTAINER="$(docker compose ps -q qwen-tts)"

if [[ -z "$QWEN_CONTAINER" ]]; then
  echo "ERROR: Qwen container is not running."
  exit 1
fi

mapfile -t CHUNK_INFO < <(
  docker compose exec -T qwen-tts \
    python - "$SYNTHESIS_B64" <<'PY'
from __future__ import annotations

import base64
import sys

from voice_service.core.config import Settings
from voice_service.services.text_chunker import TextChunker


text = base64.b64decode(
    sys.argv[1]
).decode("utf-8")

settings = Settings()

chunker = TextChunker(
    target_chars=settings.target_chunk_chars,
    max_chars=settings.max_chunk_chars,
)

# Boundary selection does not depend on the seed.
# 61 is used solely to produce a structurally valid TextChunk.
chunks = chunker.chunk(
    text,
    base_seed=61,
)

if not chunks:
    raise SystemExit("Qwen produced no text chunks")

first = chunks[0]


def b64(value: str) -> str:
    return base64.b64encode(
        value.encode("utf-8")
    ).decode("ascii")


print(chunker.identity)
print(chunker.strategy_version)

print(settings.target_chunk_chars)
print(settings.max_chunk_chars)

print(len(chunks))

print(first.start_char)
print(first.end_char)
print(first.boundary)

print(b64(first.text))
PY
)

if [[ "${#CHUNK_INFO[@]}" -ne 9 ]]; then
  echo "ERROR: failed to resolve Qwen chunking."
  exit 1
fi

CHUNKING_IDENTITY="${CHUNK_INFO[0]}"
CHUNKING_VERSION="${CHUNK_INFO[1]}"

TARGET_CHARS="${CHUNK_INFO[2]}"
MAX_CHARS="${CHUNK_INFO[3]}"

LONG_FORM_CHUNK_COUNT="${CHUNK_INFO[4]}"

FIRST_START="${CHUNK_INFO[5]}"
FIRST_END="${CHUNK_INFO[6]}"
FIRST_BOUNDARY="${CHUNK_INFO[7]}"

FIRST_CHUNK_B64="${CHUNK_INFO[8]}"

echo "Chunking:"
echo "  version:          $CHUNKING_VERSION"
echo "  identity:         $CHUNKING_IDENTITY"
echo "  target chars:     $TARGET_CHARS"
echo "  max chars:        $MAX_CHARS"
echo "  long-form chunks: $LONG_FORM_CHUNK_COUNT"

echo
echo "First chunk:"
echo "  chars:            [$FIRST_START, $FIRST_END)"
echo "  boundary:         $FIRST_BOUNDARY"


# ============================================================
# Plan
# ============================================================

echo
echo "============================================================"
echo " NeoVids - Speaker Drift Reproducibility"
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
echo "Long-form seeds:"
for seed in "${LONG_FORM_SEEDS[@]}"; do
  echo "  - $seed"
done

echo
echo "Isolated first-chunk seeds:"
for seed in "${ISOLATED_SEEDS[@]}"; do
  echo "  - $seed"
done

echo
echo "Repetitions:"
echo "  $REPETITIONS"

echo
echo "Matrix:"
echo "  long_form:            $EXPECTED_LONG_FORM"
echo "  first_chunk_isolated: $EXPECTED_ISOLATED"
echo "  total:                $EXPECTED_TOTAL"

echo
echo "Each repetition uses:"
echo "  - same text"
echo "  - same requested seed"
echo "  - same reference"
echo "  - same generation profile"
echo "  - same Qwen chunking"
echo "  - a DIFFERENT benchmark Idempotency-Key"

echo
echo "No production configuration will be modified."


if [[ "$RUN" != true ]]; then
  echo
  echo "============================================================"
  echo " PLAN COMPLETE"
  echo "============================================================"
  echo
  echo "No synthesis was performed."
  echo
  echo "Recommended clean-session execution:"
  echo
  echo "  ./run_speaker_drift_repro.sh --run --restart-qwen"
  echo
  exit 0
fi


# ============================================================
# Optional controlled ONE-TIME restart.
# ============================================================

if [[ "$RESTART_QWEN" == true ]]; then

  echo
  echo "============================================================"
  echo " CONTROLLED QWEN RESTART"
  echo "============================================================"
  echo

  cd "$QWEN_DIR"

  docker compose restart qwen-tts

  echo
  echo "Waiting for Qwen readiness..."

  READY=false

  for _ in $(seq 1 120); do
    CODE="$(
      curl -sS \
        -o /tmp/neovids-qwen-ready.json \
        -w '%{http_code}' \
        "$QWEN_READY_URL" \
        || true
    )"

    if [[ "$CODE" == "200" ]]; then
      READY=true
      break
    fi

    sleep 2
  done

  if [[ "$READY" != true ]]; then
    echo "ERROR: Qwen did not become ready."
    echo

    cat /tmp/neovids-qwen-ready.json 2>/dev/null || true

    echo
    echo "Recent Qwen logs:"

    docker compose logs \
      --tail=100 \
      qwen-tts || true

    exit 1
  fi

  echo "Qwen ready."

fi


# ============================================================
# Qwen readiness even without restart
# ============================================================

CODE="$(
  curl -sS \
    -o /tmp/neovids-qwen-ready.json \
    -w '%{http_code}' \
    "$QWEN_READY_URL" \
    || true
)"

if [[ "$CODE" != "200" ]]; then
  echo "ERROR: Qwen readiness returned HTTP $CODE"
  cat /tmp/neovids-qwen-ready.json || true
  exit 1
fi


# ============================================================
# Run ID
# ============================================================

if [[ -n "$RESUME_ID" ]]; then

  RUN_ID="$RESUME_ID"

else

  RANDOM_SUFFIX="$(
    python3 - <<'PY'
import secrets
print(secrets.token_hex(4))
PY
  )"

  RUN_ID="$(
    date -u +%Y%m%dT%H%M%SZ
  )-${RANDOM_SUFFIX}"

fi

RUN_PATH="$OUTPUT_ROOT/$RUN_ID"


# ============================================================
# GPU experiment
#
# Runs inside Orchestrator API because:
#
# - httpx already exists;
# - /data/audio persists;
# - Qwen's configured Docker URL is already correct;
# - no host Python dependencies are required.
# ============================================================

echo
echo "============================================================"
echo " REAL GPU RUN"
echo "============================================================"
echo

echo "Run ID:"
echo "  $RUN_ID"

echo
echo "Output:"
echo "  $RUN_PATH"

echo


cd "$ORCH_DIR"


docker compose exec -T api \
  python - \
  "$RUN_PATH" \
  "$RUN_ID" \
  "$REFERENCE" \
  "$GENERATION_PROFILE" \
  "$SYNTHESIS_LANGUAGE" \
  "$CANONICAL_B64" \
  "$NORMALIZED_B64" \
  "$SYNTHESIS_B64" \
  "$FIRST_CHUNK_B64" \
  "$NORMALIZER_VERSION" \
  "$NORMALIZER_IDENTITY" \
  "$LEXICON_IDENTITY" \
  "$CHUNKING_VERSION" \
  "$CHUNKING_IDENTITY" \
  "$REPETITIONS" \
  "${LONG_FORM_SEEDS[@]}" \
  --isolated \
  "${ISOLATED_SEEDS[@]}" <<'PY'
from __future__ import annotations

import base64
import csv
import hashlib
import json
import os
import random
import shutil
import sys
import time

from pathlib import Path
from urllib.parse import urljoin

import httpx

from app.core.config import Settings


# ============================================================
# Arguments
# ============================================================

args = list(sys.argv[1:])

isolated_marker = args.index("--isolated")

prefix = args[:isolated_marker]
isolated_seed_args = args[isolated_marker + 1:]

(
    run_path_raw,
    run_id,
    reference,
    generation_profile,
    synthesis_language,
    canonical_b64,
    normalized_b64,
    synthesis_b64,
    first_chunk_b64,
    normalizer_version,
    normalizer_identity,
    lexicon_identity,
    chunking_version,
    chunking_identity,
    repetitions_raw,
    *long_seed_args,
) = prefix

run_dir = Path(run_path_raw)

repetitions = int(repetitions_raw)

long_seeds = tuple(
    int(value)
    for value in long_seed_args
)

isolated_seeds = tuple(
    int(value)
    for value in isolated_seed_args
)


def decode(value: str) -> str:
    return base64.b64decode(
        value.encode("ascii")
    ).decode("utf-8")


canonical_text = decode(canonical_b64)
normalized_text = decode(normalized_b64)
synthesis_text = decode(synthesis_b64)
first_chunk_text = decode(first_chunk_b64)


# ============================================================
# Semantic configuration
# ============================================================

settings = Settings(_env_file=None)

qwen_url = settings.voice_api_url.rstrip("/") + "/"

reference_path = Path("/data/references") / reference

if not reference_path.is_file():
    raise SystemExit(
        f"reference is unavailable: {reference_path}"
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for block in iter(
            lambda: handle.read(1024 * 1024),
            b"",
        ):
            digest.update(block)

    return digest.hexdigest()


reference_sha256 = sha256_file(reference_path)

canonical_sha256 = hashlib.sha256(
    canonical_text.encode("utf-8")
).hexdigest()

synthesis_sha256 = hashlib.sha256(
    synthesis_text.encode("utf-8")
).hexdigest()

first_chunk_sha256 = hashlib.sha256(
    first_chunk_text.encode("utf-8")
).hexdigest()


# ============================================================
# Run configuration identity
# ============================================================

config_payload = {
    "schema": "neovids_speaker_drift_repro_v1",
    "reference": reference,
    "reference_sha256": reference_sha256,
    "generation_profile": generation_profile,
    "clone_mode": "x_vector_only",
    "language": synthesis_language,
    "canonical_sha256": canonical_sha256,
    "synthesis_sha256": synthesis_sha256,
    "first_chunk_sha256": first_chunk_sha256,
    "normalizer_version": normalizer_version,
    "normalizer_identity": normalizer_identity,
    "lexicon_identity": lexicon_identity,
    "chunking_version": chunking_version,
    "chunking_identity": chunking_identity,
    "long_form_seeds": list(long_seeds),
    "isolated_seeds": list(isolated_seeds),
    "repetitions": repetitions,
}

config_identity = hashlib.sha256(
    json.dumps(
        config_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()


# ============================================================
# Run directory / resume protection
# ============================================================

run_dir.mkdir(
    parents=True,
    exist_ok=True,
)

audio_root = run_dir / "audio"

manifest_path = run_dir / "manifest.jsonl"
run_json_path = run_dir / "run.json"

if run_json_path.exists():

    previous = json.loads(
        run_json_path.read_text(
            encoding="utf-8"
        )
    )

    if (
        previous.get("configuration_identity")
        != config_identity
    ):
        raise SystemExit(
            "RUN_ID exists but experiment configuration "
            "does not match. Refusing unsafe resume."
        )

else:

    run_json = {
        **config_payload,
        "run_id": run_id,
        "configuration_identity": config_identity,
        "qwen_url": qwen_url,
        "created_at_epoch": time.time(),
        "production_promoted": False,
    }

    temporary = run_json_path.with_suffix(
        ".json.tmp"
    )

    temporary.write_text(
        json.dumps(
            run_json,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    temporary.replace(run_json_path)


# ============================================================
# Load existing completed benchmark cells
# ============================================================

existing: dict[str, dict] = {}

if manifest_path.exists():

    with manifest_path.open(
        "r",
        encoding="utf-8",
    ) as handle:

        for line in handle:

            line = line.strip()

            if not line:
                continue

            row = json.loads(line)

            existing[row["cell_id"]] = row


# ============================================================
# Experiment matrix
# ============================================================

cells = []

for seed in long_seeds:

    for repetition in range(
        1,
        repetitions + 1,
    ):

        cells.append(
            {
                "kind": "long_form",
                "seed": seed,
                "repetition": repetition,
                "text": synthesis_text,
            }
        )


for seed in isolated_seeds:

    for repetition in range(
        1,
        repetitions + 1,
    ):

        cells.append(
            {
                "kind": "first_chunk_isolated",
                "seed": seed,
                "repetition": repetition,
                "text": first_chunk_text,
            }
        )


for cell in cells:

    cell["cell_id"] = (
        f"{cell['kind']}:"
        f"{cell['seed']}:"
        f"r{cell['repetition']}"
    )


# ------------------------------------------------------------
# Deterministically shuffle generation order.
#
# This avoids always testing the same seed in the same thermal /
# process-state position.
# ------------------------------------------------------------

order_seed = int.from_bytes(
    hashlib.sha256(
        (
            run_id
            + "|speaker-drift-generation-order-v1"
        ).encode("utf-8")
    ).digest()[:8],
    "big",
)

rng = random.Random(order_seed)

rng.shuffle(cells)


# ============================================================
# Helper
# ============================================================

def append_jsonl(
    path: Path,
    payload: dict,
) -> None:

    with path.open(
        "a",
        encoding="utf-8",
    ) as handle:

        handle.write(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
        )

        handle.flush()

        os.fsync(
            handle.fileno()
        )


def safe_error(response: httpx.Response) -> str:

    try:
        payload = response.json()
    except Exception:
        return f"HTTP {response.status_code}"

    detail = payload.get("detail")

    return (
        str(detail)
        if detail
        else f"HTTP {response.status_code}"
    )


# ============================================================
# Generation
# ============================================================

total = len(cells)

started_all = time.monotonic()

with httpx.Client(
    base_url=qwen_url,
    timeout=30.0,
) as client:

    for sequence_index, cell in enumerate(
        cells,
        start=1,
    ):

        cell_id = cell["cell_id"]

        existing_row = existing.get(cell_id)

        if existing_row is not None:

            status = existing_row.get(
                "generation_status"
            )

            wav = existing_row.get("wav")

            if (
                status == "success"
                and wav
                and (run_dir / wav).is_file()
            ):

                print(
                    f"[{sequence_index:02d}/{total:02d}] "
                    f"{cell_id} SKIP valid completed cell",
                    flush=True,
                )

                continue

            if status == "failed":

                print(
                    f"[{sequence_index:02d}/{total:02d}] "
                    f"{cell_id} SKIP recorded failure",
                    flush=True,
                )

                continue


        seed = int(cell["seed"])

        repetition = int(
            cell["repetition"]
        )

        kind = str(cell["kind"])

        text = str(cell["text"])


        # ----------------------------------------------------
        # IMPORTANT:
        #
        # Different repetition -> different Idempotency-Key.
        #
        # Same run + same cell -> same key, allowing safe
        # resumption after interruption.
        #
        # The key is NOT inserted into the Qwen generation
        # fingerprint or generation controls.
        # ----------------------------------------------------

        idempotency_key = (
            f"drift-v1:"
            f"{run_id}:"
            f"{kind}:"
            f"{seed}:"
            f"r{repetition}"
        )


        row = {
            "schema": "neovids_speaker_drift_cell_v1",
            "run_id": run_id,
            "configuration_identity": config_identity,
            "cell_id": cell_id,
            "sequence_index": sequence_index,
            "kind": kind,
            "requested_seed": seed,
            "repetition": repetition,
            "reference": reference,
            "reference_sha256": reference_sha256,
            "clone_mode": "x_vector_only",
            "generation_profile": generation_profile,
            "language": synthesis_language,
            "idempotency_key": idempotency_key,
            "canonical_text_sha256": canonical_sha256,
            "synthesis_text_sha256": (
                synthesis_sha256
                if kind == "long_form"
                else first_chunk_sha256
            ),
            "normalizer_version": normalizer_version,
            "normalizer_identity": normalizer_identity,
            "lexicon_identity": lexicon_identity,
            "chunking_version": chunking_version,
            "chunking_identity": chunking_identity,
            "generation_status": "pending",
        }


        print(
            f"[{sequence_index:02d}/{total:02d}] "
            f"kind={kind} "
            f"seed={seed} "
            f"rep={repetition} "
            f"stage=create",
            flush=True,
        )


        cell_started = time.monotonic()


        # ----------------------------------------------------
        # Create / resume Qwen job
        # ----------------------------------------------------

        try:

            response = client.post(
                "v1/benchmark/narrations",
                headers={
                    "Idempotency-Key":
                        idempotency_key,
                },
                json={
                    "text": text,
                    "language":
                        synthesis_language,
                    "reference_id":
                        reference,
                    "generation_profile":
                        generation_profile,
                    "seed":
                        seed,
                },
            )

        except Exception as exc:

            row.update(
                {
                    "generation_status":
                        "failed",
                    "failure_stage":
                        "create_transport",
                    "failure_reason":
                        type(exc).__name__,
                    "elapsed_seconds":
                        round(
                            time.monotonic()
                            - cell_started,
                            6,
                        ),
                }
            )

            append_jsonl(
                manifest_path,
                row,
            )

            existing[cell_id] = row

            print(
                "  FAILED create transport",
                flush=True,
            )

            continue


        if response.status_code != 202:

            row.update(
                {
                    "generation_status":
                        "failed",
                    "failure_stage":
                        "create_http",
                    "failure_reason":
                        safe_error(response),
                    "http_status":
                        response.status_code,
                    "elapsed_seconds":
                        round(
                            time.monotonic()
                            - cell_started,
                            6,
                        ),
                }
            )

            append_jsonl(
                manifest_path,
                row,
            )

            existing[cell_id] = row

            print(
                f"  FAILED create "
                f"HTTP {response.status_code}",
                flush=True,
            )

            continue


        created = response.json()

        job_id = created["id"]

        row["job_id"] = job_id

        row["created"] = created.get(
            "created"
        )

        row["resolved_seed"] = created.get(
            "resolved_seed"
        )


        # Explicit-seed benchmark invariant.
        if (
            row["resolved_seed"]
            is not None
            and int(row["resolved_seed"])
            != seed
        ):
            raise SystemExit(
                f"Qwen resolved seed mismatch for {cell_id}: "
                f"requested={seed} "
                f"resolved={row['resolved_seed']}"
            )


        print(
            f"  job={job_id} "
            f"created={row['created']} "
            f"stage=poll",
            flush=True,
        )


        # ----------------------------------------------------
        # Poll
        # ----------------------------------------------------

        deadline = (
            time.monotonic()
            + settings.voice_job_timeout_seconds
        )

        terminal = None

        while time.monotonic() < deadline:

            try:

                status_response = client.get(
                    f"v1/narrations/{job_id}",
                    timeout=
                        settings.voice_status_timeout_seconds,
                )

            except Exception:

                time.sleep(
                    settings.voice_poll_interval_seconds
                )

                continue


            if status_response.status_code != 200:

                time.sleep(
                    settings.voice_poll_interval_seconds
                )

                continue


            status_payload = (
                status_response.json()
            )

            status = status_payload.get(
                "status"
            )

            if status in {
                "completed",
                "failed",
            }:
                terminal = status_payload
                break

            time.sleep(
                settings.voice_poll_interval_seconds
            )


        if terminal is None:

            row.update(
                {
                    "generation_status":
                        "failed",
                    "failure_stage":
                        "poll_timeout",
                    "failure_reason":
                        "voice_job_timeout",
                    "elapsed_seconds":
                        round(
                            time.monotonic()
                            - cell_started,
                            6,
                        ),
                }
            )

            append_jsonl(
                manifest_path,
                row,
            )

            existing[cell_id] = row

            print(
                "  FAILED timeout",
                flush=True,
            )

            continue


        row["qwen_status"] = (
            terminal.get("status")
        )

        row["resolved_seed"] = (
            terminal.get("resolved_seed")
        )

        row["model_id"] = (
            terminal.get("model_id")
        )

        row["model_revision"] = (
            terminal.get("model_revision")
        )

        row["voice_profile"] = (
            terminal.get("voice_profile")
        )

        row["terminal_metadata"] = (
            terminal.get("metadata") or {}
        )


        if terminal["status"] == "failed":

            row.update(
                {
                    "generation_status":
                        "failed",
                    "failure_stage":
                        "qwen_generation",
                    "failure_category":
                        terminal.get(
                            "error_category"
                        ),
                    "failure_reason":
                        terminal.get(
                            "error_message"
                        ),
                    "elapsed_seconds":
                        round(
                            time.monotonic()
                            - cell_started,
                            6,
                        ),
                }
            )

            append_jsonl(
                manifest_path,
                row,
            )

            existing[cell_id] = row

            print(
                "  FAILED Qwen "
                f"{row.get('failure_category')}: "
                f"{row.get('failure_reason')}",
                flush=True,
            )

            continue


        # ----------------------------------------------------
        # Download
        # ----------------------------------------------------

        audio_url = terminal.get(
            "audio_url"
        )

        if not audio_url:

            row.update(
                {
                    "generation_status":
                        "failed",
                    "failure_stage":
                        "artifact",
                    "failure_reason":
                        "completed_without_audio_url",
                }
            )

            append_jsonl(
                manifest_path,
                row,
            )

            existing[cell_id] = row

            continue


        absolute_audio_url = urljoin(
            qwen_url,
            audio_url.lstrip("/"),
        )

        try:

            audio_response = httpx.get(
                absolute_audio_url,
                timeout=
                    settings.voice_download_timeout_seconds,
            )

            audio_response.raise_for_status()

            audio_bytes = (
                audio_response.content
            )

        except Exception as exc:

            row.update(
                {
                    "generation_status":
                        "failed",
                    "failure_stage":
                        "download",
                    "failure_reason":
                        type(exc).__name__,
                    "elapsed_seconds":
                        round(
                            time.monotonic()
                            - cell_started,
                            6,
                        ),
                }
            )

            append_jsonl(
                manifest_path,
                row,
            )

            existing[cell_id] = row

            continue


        if not audio_bytes.startswith(b"RIFF"):

            row.update(
                {
                    "generation_status":
                        "failed",
                    "failure_stage":
                        "artifact_validation",
                    "failure_reason":
                        "download_is_not_riff_wave",
                }
            )

            append_jsonl(
                manifest_path,
                row,
            )

            existing[cell_id] = row

            continue


        artifact_sha256 = hashlib.sha256(
            audio_bytes
        ).hexdigest()


        expected_sha256 = terminal.get(
            "result_sha256"
        )

        if (
            expected_sha256
            and expected_sha256
            != artifact_sha256
        ):

            row.update(
                {
                    "generation_status":
                        "failed",
                    "failure_stage":
                        "artifact_validation",
                    "failure_reason":
                        "sha256_mismatch",
                    "artifact_sha256":
                        artifact_sha256,
                    "expected_sha256":
                        expected_sha256,
                }
            )

            append_jsonl(
                manifest_path,
                row,
            )

            existing[cell_id] = row

            continue


        destination = (
            audio_root
            / kind
            / f"seed_{seed}"
            / f"rep_{repetition:02d}.wav"
        )

        destination.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        temporary = destination.with_suffix(
            ".wav.tmp"
        )

        temporary.write_bytes(
            audio_bytes
        )

        temporary.replace(
            destination
        )


        row.update(
            {
                "generation_status":
                    "success",
                "artifact_sha256":
                    artifact_sha256,
                "artifact_bytes":
                    len(audio_bytes),
                "wav":
                    str(
                        destination.relative_to(
                            run_dir
                        )
                    ),
                "result_duration_seconds":
                    terminal.get(
                        "result_duration_seconds"
                    ),
                "result_sample_rate":
                    terminal.get(
                        "result_sample_rate"
                    ),
                "result_frame_count":
                    terminal.get(
                        "result_frame_count"
                    ),
                "elapsed_seconds":
                    round(
                        time.monotonic()
                        - cell_started,
                        6,
                    ),
            }
        )


        append_jsonl(
            manifest_path,
            row,
        )

        existing[cell_id] = row


        print(
            "  COMPLETE "
            f"duration="
            f"{row.get('result_duration_seconds')} "
            f"sha={artifact_sha256[:12]}",
            flush=True,
        )


# ============================================================
# Final records
# ============================================================

records = {}

with manifest_path.open(
    "r",
    encoding="utf-8",
) as handle:

    for line in handle:

        if not line.strip():
            continue

        row = json.loads(line)

        records[row["cell_id"]] = row


ordered_records = [
    records[cell["cell_id"]]
    for cell in cells
    if cell["cell_id"] in records
]


successes = [
    row
    for row in ordered_records
    if row.get("generation_status")
    == "success"
]

failures = [
    row
    for row in ordered_records
    if row.get("generation_status")
    != "success"
]


# ============================================================
# Determinism summary
# ============================================================

groups: dict[
    tuple[str, int],
    list[dict],
] = {}

for row in ordered_records:

    key = (
        row["kind"],
        int(row["requested_seed"]),
    )

    groups.setdefault(
        key,
        [],
    ).append(row)


summary_rows = []

for (
    kind,
    seed,
), rows in sorted(groups.items()):

    good = [
        row
        for row in rows
        if row.get("generation_status")
        == "success"
    ]

    hashes = {
        row.get("artifact_sha256")
        for row in good
        if row.get("artifact_sha256")
    }

    durations = [
        float(row["result_duration_seconds"])
        for row in good
        if row.get("result_duration_seconds")
        is not None
    ]

    summary_rows.append(
        {
            "kind": kind,
            "seed": seed,
            "planned_repetitions":
                repetitions,
            "successful_repetitions":
                len(good),
            "failed_repetitions":
                len(rows) - len(good),
            "distinct_artifact_sha256":
                len(hashes),
            "byte_identical_across_successes":
                (
                    len(good) > 1
                    and len(hashes) == 1
                ),
            "minimum_duration_seconds":
                min(durations)
                if durations
                else None,
            "maximum_duration_seconds":
                max(durations)
                if durations
                else None,
        }
    )


summary = {
    "schema":
        "neovids_speaker_drift_summary_v1",
    "run_id":
        run_id,
    "configuration_identity":
        config_identity,
    "planned":
        len(cells),
    "successful":
        len(successes),
    "failed":
        len(failures),
    "elapsed_seconds":
        round(
            time.monotonic()
            - started_all,
            6,
        ),
    "groups":
        summary_rows,
}

summary_path = (
    run_dir / "summary.json"
)

summary_path.write_text(
    json.dumps(
        summary,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)


with (
    run_dir / "summary.csv"
).open(
    "w",
    newline="",
    encoding="utf-8",
) as handle:

    fields = [
        "kind",
        "seed",
        "planned_repetitions",
        "successful_repetitions",
        "failed_repetitions",
        "distinct_artifact_sha256",
        "byte_identical_across_successes",
        "minimum_duration_seconds",
        "maximum_duration_seconds",
    ]

    writer = csv.DictWriter(
        handle,
        fieldnames=fields,
    )

    writer.writeheader()

    writer.writerows(
        summary_rows
    )


# ============================================================
# Blind listening pack
# ============================================================

blind_dir = run_dir / "blind"

private_dir = (
    blind_dir / "private"
)

if blind_dir.exists():

    shutil.rmtree(
        blind_dir
    )

blind_dir.mkdir(
    parents=True
)

private_dir.mkdir(
    parents=True
)


# Stable seed → blind label mapping.
all_seeds = tuple(
    sorted(
        set(long_seeds)
        | set(isolated_seeds)
    )
)

labels = [
    chr(
        ord("A") + index
    )
    for index in range(
        len(all_seeds)
    )
]

mapping_rng = random.Random(
    int.from_bytes(
        hashlib.sha256(
            (
                run_id
                + "|speaker-drift-blind-map-v1"
            ).encode("utf-8")
        ).digest()[:8],
        "big",
    )
)

mapping_rng.shuffle(
    labels
)

seed_to_label = dict(
    zip(
        all_seeds,
        labels,
        strict=True,
    )
)


with (
    private_dir
    / "seed_map_PRIVATE.csv"
).open(
    "w",
    newline="",
    encoding="utf-8",
) as handle:

    writer = csv.writer(
        handle
    )

    writer.writerow(
        [
            "blind_seed",
            "seed",
        ]
    )

    for seed in all_seeds:

        writer.writerow(
            [
                seed_to_label[seed],
                seed,
            ]
        )


# ------------------------------------------------------------
# Private machine evidence
# ------------------------------------------------------------

with (
    private_dir
    / "manifest_PRIVATE.jsonl"
).open(
    "w",
    encoding="utf-8",
) as handle:

    for row in ordered_records:

        private_row = {
            **row,
            "blind_seed":
                seed_to_label[
                    int(
                        row["requested_seed"]
                    )
                ],
        }

        handle.write(
            json.dumps(
                private_row,
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
        )


# ------------------------------------------------------------
# Public blind WAV tree / worksheet
# ------------------------------------------------------------

listening_rows = []

for row in ordered_records:

    seed = int(
        row["requested_seed"]
    )

    label = seed_to_label[
        seed
    ]

    kind = row["kind"]

    repetition = int(
        row["repetition"]
    )

    wav_relative = ""

    if (
        row.get("generation_status")
        == "success"
        and row.get("wav")
    ):

        source = (
            run_dir
            / row["wav"]
        )

        if source.is_file():

            destination_dir = (
                blind_dir
                / kind
                / f"rep_{repetition:02d}"
            )

            destination_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            destination = (
                destination_dir
                / f"{label}.wav"
            )

            try:

                os.link(
                    source,
                    destination,
                )

            except OSError:

                shutil.copy2(
                    source,
                    destination,
                )

            wav_relative = str(
                destination.relative_to(
                    run_dir
                )
            )


    listening_rows.append(
        {
            "kind":
                kind,
            "repetition":
                repetition,
            "blind_seed":
                label,
            "wav":
                wav_relative,
            "machine_generation_status":
                row.get(
                    "generation_status",
                    "",
                ),
            "speaker_identity_stability_1_5":
                "",
            "ptbr_accent_1_5":
                "",
            "naturalness_1_5":
                "",
            "pace_1_5":
                "",
            "rhythm_prosody_1_5":
                "",
            "pronunciation_1_5":
                "",
            "overall_1_5":
                "",
            "wrong_speaker":
                "",
            "female_voice":
                "",
            "ptpt_drift":
                "",
            "drift_start_seconds":
                "",
            "drift_end_seconds":
                "",
            "catastrophic_failure":
                (
                    "yes"
                    if row.get(
                        "generation_status"
                    )
                    != "success"
                    else ""
                ),
            "failure_type":
                (
                    "generation_failure"
                    if row.get(
                        "generation_status"
                    )
                    != "success"
                    else ""
                ),
            "notes":
                "",
        }
    )


# ------------------------------------------------------------
# Shuffle listening order inside each kind/repetition.
# ------------------------------------------------------------

listening_groups = {}

for row in listening_rows:

    key = (
        row["kind"],
        row["repetition"],
    )

    listening_groups.setdefault(
        key,
        [],
    ).append(row)


final_listening = []

for key in sorted(
    listening_groups
):

    group = listening_groups[
        key
    ]

    local_rng = random.Random(
        int.from_bytes(
            hashlib.sha256(
                (
                    run_id
                    + "|"
                    + repr(key)
                    + "|speaker-drift-play-order-v1"
                ).encode("utf-8")
            ).digest()[:8],
            "big",
        )
    )

    local_rng.shuffle(
        group
    )

    for index, row in enumerate(
        group,
        start=1,
    ):

        row["play_order"] = (
            index
        )

        final_listening.append(
            row
        )


fields = [
    "kind",
    "repetition",
    "play_order",
    "blind_seed",
    "wav",
    "machine_generation_status",
    "speaker_identity_stability_1_5",
    "ptbr_accent_1_5",
    "naturalness_1_5",
    "pace_1_5",
    "rhythm_prosody_1_5",
    "pronunciation_1_5",
    "overall_1_5",
    "wrong_speaker",
    "female_voice",
    "ptpt_drift",
    "drift_start_seconds",
    "drift_end_seconds",
    "catastrophic_failure",
    "failure_type",
    "notes",
]


with (
    blind_dir
    / "listening.csv"
).open(
    "w",
    newline="",
    encoding="utf-8",
) as handle:

    writer = csv.DictWriter(
        handle,
        fieldnames=fields,
    )

    writer.writeheader()

    writer.writerows(
        final_listening
    )


# ============================================================
# Human review guide
# ============================================================

(blind_dir / "README.txt").write_text(
    """NeoVids - Speaker Drift Reproducibility Blind Review

REFERENCE IS FIXED.
GENERATION PROFILE IS FIXED.
ONLY SEED / INDEPENDENT REPETITION IS UNDER TEST.

DO NOT OPEN:

private/seed_map_PRIVATE.csv
private/manifest_PRIVATE.jsonl

until listening is complete.

The same letter always represents the same seed.

============================================================
TEST 1 — LONG_FORM
============================================================

Each candidate seed appears three times as an independent Qwen
job with the exact same synthesis input.

Primary question:

Does the same seed repeatedly produce the same speaker identity,
or can it move into another speaker / female voice / PT-PT?

Record:

wrong_speaker
female_voice
ptpt_drift

as yes/no.

If drift occurs only during part of the WAV, approximately record:

drift_start_seconds
drift_end_seconds

============================================================
TEST 2 — FIRST_CHUNK_ISOLATED
============================================================

This uses the exact synthesis text that Qwen's current chunker
selects as chunk zero of LONG_FORM.

The same BASE seed is submitted.

Therefore Qwen derives the same provider seed for chunk index zero
as it does in the full LONG_FORM request.

Interpretation:

A) drift also occurs when isolated

Likely evidence that the problem belongs to the local
text + seed + speaker-conditioning generation trajectory.

B) isolated chunk is consistently correct, but LONG_FORM chunk zero
drifts

Stronger evidence that long-form execution/state/context or another
multi-chunk path deserves investigation.

============================================================
SCORING
============================================================

5 = excellent
4 = good
3 = acceptable
2 = poor
1 = unusable

speaker_identity_stability_1_5:

5 = expected speaker through the entire WAV
4 = tiny harmless timbre variation
3 = noticeable but still recognizably same speaker
2 = major identity change
1 = clearly another speaker / wrong voice

catastrophic_failure=yes for:

- wrong speaker;
- female/wrong-gender voice;
- major PT-PT speaker drift;
- speaker identity change inside the WAV;
- runaway;
- severe missing/repeated content;
- machine generation failure.

Do not choose based on loudness.

The goal of this benchmark is REPRODUCIBILITY,
not selecting the nicest individual WAV.
""",
    encoding="utf-8",
)


print()
print("=" * 68)
print("BENCHMARK COMPLETE")
print("=" * 68)

print(
    f"planned={len(cells)} "
    f"successful={len(successes)} "
    f"failed={len(failures)} "
    f"elapsed={summary['elapsed_seconds']:.1f}s"
)

print()
print(f"run={run_dir}")
print(
    f"listening={blind_dir / 'listening.csv'}"
)

print(
    "private_map="
    f"{private_dir / 'seed_map_PRIVATE.csv'}"
)

print()
print("Repetition SHA summary:")

for item in summary_rows:

    print(
        f"  {item['kind']:22s} "
        f"seed={item['seed']:8d} "
        f"ok={item['successful_repetitions']}/"
        f"{item['planned_repetitions']} "
        f"distinct_sha="
        f"{item['distinct_artifact_sha256']}"
    )
PY


# ============================================================
# Ensure output exists
# ============================================================

if ! docker compose exec -T api \
    test -f "$RUN_PATH/run.json"; then
  echo
  echo "ERROR: benchmark run.json missing."
  exit 1
fi


# ============================================================
# Export archive
# ============================================================

echo
echo "============================================================"
echo " EXPORTING"
echo "============================================================"
echo

mkdir -p "$EXPORT_DIR"

ARCHIVE="${RUN_ID}-speaker-drift-repro.tar.gz"

docker compose exec -T api sh -lc "
  cd '$(dirname "$RUN_PATH")' &&
  tar -czf '/tmp/$ARCHIVE' '$RUN_ID'
"

docker cp \
  "$API_CONTAINER:/tmp/$ARCHIVE" \
  "$EXPORT_DIR/$ARCHIVE"

docker compose exec -T api \
  rm -f "/tmp/$ARCHIVE" \
  >/dev/null 2>&1 || true


echo
echo "============================================================"
echo " COMPLETE"
echo "============================================================"
echo

echo "Run ID:"
echo "  $RUN_ID"

echo
echo "Matrix:"
echo "  long_form:            $EXPECTED_LONG_FORM"
echo "  first_chunk_isolated: $EXPECTED_ISOLATED"
echo "  total:                $EXPECTED_TOTAL"

echo
echo "Host archive:"
echo "  $EXPORT_DIR/$ARCHIVE"

echo
echo "Blind listening:"
echo "  $RUN_PATH/blind/listening.csv"

echo
echo "DO NOT OPEN UNTIL LISTENING IS COMPLETE:"
echo "  $RUN_PATH/blind/private/seed_map_PRIVATE.csv"

echo
echo "If interrupted, resume with:"
echo
echo "  ./run_speaker_drift_repro.sh --resume $RUN_ID"

echo
