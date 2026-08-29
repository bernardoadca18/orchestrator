#!/usr/bin/env bash
set -Eeuo pipefail

# ============================================================
# NeoVids - Naturalness / Rhythm Temperature Benchmark v2
#
# Frozen:
#   reference = ref_full_processed_raw.wav
#   clone     = x_vector_only
#   seed      = 303
#
# Profiles:
#   baseline  main=.90 sub=.90  (historical)
#   main095   main=.95 sub=.90
#   main100   main=1.00 sub=.90
#   sub095    main=.90 sub=.95
#   sub100    main=.90 sub=1.00
#
# Cases:
#   natural_ptbr
#   gameplay_narration
#   controlled_intensity
#   mixed_english
#   long_form
#
# New synthesis: 4 × 5 = 20
# Historical baseline: 5
# Blind pack: 25
#
# IMPORTANT:
# Every Qwen recreation includes the official NeoVids infra
# override so qwen-voice remains reachable through network
# "neovids".
# ============================================================

ORCH_DIR="${ORCH_DIR:-$HOME/workspaces/neovids/orchestrator}"
QWEN_DIR="${QWEN_DIR:-$HOME/workspaces/neovids/qwen3-tts-workshop}"
INFRA_DIR="${INFRA_DIR:-$HOME/workspaces/neovids/neovids-infra}"

QWEN_BASE="$QWEN_DIR/docker-compose.yaml"
QWEN_INFRA="$INFRA_DIR/infra/orchestration/qwen3-tts-workshop.yml"

EXPORT_DIR="$ORCH_DIR/benchmark_exports"

REFERENCE="ref_full_processed_raw.wav"
REFERENCE_SHA256="8856924e05f76d1a3a5b8554218f80b5e1a898aebfe211ec1b65b60eabdbc70b"

SEED=303
BASELINE_PROFILE="narration_v1"

PROFILE_MAIN095="narration_nat_main095_v1"
PROFILE_MAIN100="narration_nat_main100_v1"
PROFILE_SUB095="narration_nat_sub095_v1"
PROFILE_SUB100="narration_nat_sub100_v1"

PROFILES=(
  "$PROFILE_MAIN095"
  "$PROFILE_MAIN100"
  "$PROFILE_SUB095"
  "$PROFILE_SUB100"
)

CASES=(
  natural_ptbr
  gameplay_narration
  controlled_intensity
  mixed_english
  long_form
)

BASELINE_RUN="/data/audio/benchmarks/reference-regression/20260829T035529Z-35a384d7"

NEW_ROOT="/data/audio/benchmarks/naturalness-temperature-runs"
PACK_ROOT="/data/audio/benchmarks/naturalness-temperature"

RUN=false

if [[ "${1:-}" == "--run" ]]; then
  RUN=true
elif [[ $# -gt 0 ]]; then
  echo "Usage: $0 [--run]"
  exit 2
fi

for required in \
  "$ORCH_DIR" \
  "$QWEN_DIR" \
  "$INFRA_DIR"
do
  [[ -d "$required" ]] || {
    echo "ERROR: missing directory: $required"
    exit 1
  }
done

[[ -f "$QWEN_BASE" ]] || {
  echo "ERROR: missing $QWEN_BASE"
  exit 1
}

[[ -f "$QWEN_INFRA" ]] || {
  echo "ERROR: missing $QWEN_INFRA"
  exit 1
}

cd "$ORCH_DIR"

API_CONTAINER="$(docker compose ps -q api)"

[[ -n "$API_CONTAINER" ]] || {
  echo "ERROR: orchestrator api is not running"
  exit 1
}

TOKEN="$$-$(date +%s)"

TMP_PROFILE_HOST="/tmp/neovids-naturalness-profiles-$TOKEN.yaml"
TMP_OVERRIDE_HOST="/tmp/neovids-naturalness-compose-$TOKEN.yml"

TMP_PROFILE_CONTAINER="/tmp/neovids-naturalness-profiles.yaml"
TMP_CORPUS_CONTAINER="/tmp/neovids-naturalness-corpus-$TOKEN.yaml"

QWEN_EXPERIMENTAL=false


# ============================================================
# Qwen helpers
# ============================================================

wait_host_qwen() {
  local tries="${1:-180}"

  for _ in $(seq 1 "$tries"); do
    if curl -fsS \
      http://localhost:8889/health/ready \
      >/dev/null 2>&1
    then
      return 0
    fi

    sleep 2
  done

  return 1
}


restore_qwen() {
  local status=$?

  trap - EXIT INT TERM

  if [[ "$QWEN_EXPERIMENTAL" == true ]]; then
    echo
    echo "============================================================"
    echo " RESTORING PRODUCTION QWEN + NEOVIDS NETWORK"
    echo "============================================================"

    cd "$QWEN_DIR"

    docker compose \
      --project-name qwen3-tts-workshop \
      -f "$QWEN_BASE" \
      -f "$QWEN_INFRA" \
      up -d \
      --force-recreate \
      --no-deps \
      qwen-tts || true

    wait_host_qwen 180 || true
  fi

  rm -f \
    "$TMP_PROFILE_HOST" \
    "$TMP_OVERRIDE_HOST" \
    >/dev/null 2>&1 || true

  cd "$ORCH_DIR" 2>/dev/null || true

  docker compose exec -T api \
    rm -f "$TMP_CORPUS_CONTAINER" \
    >/dev/null 2>&1 || true

  exit "$status"
}

trap restore_qwen EXIT INT TERM


orchestrator_qwen_check() {
  cd "$ORCH_DIR"

  docker compose exec -T api \
    python - <<'PY_CONNECT'
import json
import urllib.request

url = "http://qwen-voice:8889/health/ready"

try:
    with urllib.request.urlopen(url, timeout=10) as response:
        payload = json.loads(
            response.read().decode("utf-8")
        )
except Exception as exc:
    raise SystemExit(
        "ORCHESTRATOR -> QWEN CONNECTIVITY FAILED: "
        f"{type(exc).__name__}: {exc}"
    )

print("HTTP:", response.status)
print("status:", payload.get("status"))
print("model:", payload.get("model_id"))
print("revision:", payload.get("model_revision"))
print("queue_depth:", payload.get("queue_depth"))

if response.status != 200:
    raise SystemExit("Qwen did not return HTTP 200")

if payload.get("status") != "ready":
    raise SystemExit("Qwen is not ready")
PY_CONNECT
}


# ============================================================
# Header
# ============================================================

echo
echo "============================================================"
echo " NeoVids - Naturalness Temperature Benchmark v2"
echo "============================================================"
echo
echo "Reference: $REFERENCE"
echo "Seed:      $SEED"
echo
echo "Baseline:  main=.90 / sub=.90"
echo "main095:   main=.95 / sub=.90"
echo "main100:   main=1.00 / sub=.90"
echo "sub095:    main=.90 / sub=.95"
echo "sub100:    main=.90 / sub=1.00"
echo
echo "New generations: 20"
echo "Baseline reuse:   5"
echo "Blind WAVs:       25"


# ============================================================
# Current topology check
# ============================================================

echo
echo "============================================================"
echo " CURRENT QWEN PREFLIGHT"
echo "============================================================"
echo

wait_host_qwen 30 || {
  echo "ERROR: Qwen is not ready from host"
  exit 1
}

echo "Networks:"

docker inspect qwen3-tts \
  --format '{{range $name,$v := .NetworkSettings.Networks}}{{println "  " $name}}{{end}}'

if ! docker inspect qwen3-tts \
  --format '{{range $name,$v := .NetworkSettings.Networks}}{{println $name}}{{end}}' \
  | grep -qx 'neovids'
then
  echo "ERROR: qwen3-tts is not attached to network neovids"
  exit 1
fi

echo
echo "Orchestrator connectivity:"

orchestrator_qwen_check


# ============================================================
# Build experimental generation registry
# ============================================================

echo
echo "============================================================"
echo " BUILDING EXPERIMENTAL GENERATION REGISTRY"
echo "============================================================"
echo

cd "$QWEN_DIR"

docker compose \
  --project-name qwen3-tts-workshop \
  -f "$QWEN_BASE" \
  -f "$QWEN_INFRA" \
  exec -T qwen-tts \
  python - \
  "$BASELINE_PROFILE" \
  "$PROFILE_MAIN095" \
  "$PROFILE_MAIN100" \
  "$PROFILE_SUB095" \
  "$PROFILE_SUB100" \
  > "$TMP_PROFILE_HOST" <<'PY_PROFILES'
from __future__ import annotations

import copy
import sys
from pathlib import Path

import yaml

baseline_name, main095, main100, sub095, sub100 = sys.argv[1:]

path = Path("/app/config/generation_profiles.yaml")

raw = yaml.safe_load(
    path.read_text(encoding="utf-8")
)

profiles = raw["generation_profiles"]
baseline = profiles[baseline_name]
params = baseline["parameters"]

expected = {
    "do_sample": True,
    "temperature": 0.9,
    "top_p": 1.0,
    "top_k": 50,
    "repetition_penalty": 1.05,
    "subtalker_dosample": True,
    "subtalker_temperature": 0.9,
    "subtalker_top_p": 1.0,
    "subtalker_top_k": 50,
    "max_new_tokens": 2048,
}

for key, wanted in expected.items():
    actual = params.get(key)

    if actual != wanted:
        raise SystemExit(
            f"baseline mismatch: {key}={actual!r}, "
            f"expected={wanted!r}"
        )

def add(
    name: str,
    main_temp: float,
    sub_temp: float,
) -> None:
    profile = copy.deepcopy(baseline)

    profile["version"] = "naturalness-temperature-v1"

    profile["parameters"]["temperature"] = main_temp
    profile["parameters"]["subtalker_temperature"] = sub_temp

    profiles[name] = profile

add(main095, 0.95, 0.90)
add(main100, 1.00, 0.90)
add(sub095, 0.90, 0.95)
add(sub100, 0.90, 1.00)

print(
    yaml.safe_dump(
        raw,
        allow_unicode=True,
        sort_keys=False,
    ),
    end="",
)
PY_PROFILES

[[ -s "$TMP_PROFILE_HOST" ]] || {
  echo "ERROR: generated profile registry is empty"
  exit 1
}


# ============================================================
# Create five-case corpus
# ============================================================

echo
echo "============================================================"
echo " PREPARING FIVE-CASE CORPUS"
echo "============================================================"
echo

cd "$ORCH_DIR"

docker compose exec -T api \
  python - \
  "$TMP_CORPUS_CONTAINER" \
  "${CASES[@]}" <<'PY_CORPUS'
from pathlib import Path
import sys
import yaml

target = Path(sys.argv[1])
wanted = tuple(sys.argv[2:])

source = Path("/app/config/reference_regression_corpus.yaml")

if not source.is_file():
    source = Path("config/reference_regression_corpus.yaml")

raw = yaml.safe_load(
    source.read_text(encoding="utf-8")
)

items = (
    list(raw.get("quick_cases") or [])
    + list(raw.get("full_cases") or [])
)

by_id = {
    str(item["id"]): item
    for item in items
}

missing = [
    case
    for case in wanted
    if case not in by_id
]

if missing:
    raise SystemExit(
        f"missing corpus cases: {missing}"
    )

payload = {
    "schema": raw["schema"],
    "version": "naturalness_temperature_v1",
    "quick_cases": [
        by_id[case]
        for case in wanted
    ],
    "full_cases": [],
}

target.write_text(
    yaml.safe_dump(
        payload,
        allow_unicode=True,
        sort_keys=False,
    ),
    encoding="utf-8",
)

for case in wanted:
    print("  -", case)
PY_CORPUS


# ============================================================
# Historical baseline check
# ============================================================

echo
echo "============================================================"
echo " BASELINE 303 PREFLIGHT"
echo "============================================================"
echo

docker compose exec -T api \
  python - \
  "$BASELINE_RUN" \
  "$REFERENCE" \
  "$REFERENCE_SHA256" \
  "$SEED" \
  "${CASES[@]}" <<'PY_BASE'
import json
import sys
from pathlib import Path

run = Path(sys.argv[1])
reference = sys.argv[2]
sha = sys.argv[3]
seed = int(sys.argv[4])
cases = tuple(sys.argv[5:])

rows = [
    json.loads(line)
    for line in (run / "manifest.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    if line.strip()
]

for case in cases:
    good = []

    for row in rows:
        if row.get("reference") != reference:
            continue
        if row.get("reference_sha256") != sha:
            continue
        if int(row.get("requested_seed", -1)) != seed:
            continue
        if row.get("case_id") != case:
            continue
        if row.get("generation_profile") != "narration_v1":
            continue
        if row.get("clone_mode") != "x_vector_only":
            continue
        if row.get("generation_status") != "success":
            continue
        if row.get("artifact_integrity_status") != "valid":
            continue

        wav = row.get("wav")

        if wav and (run / wav).is_file():
            good.append(row)

    if not good:
        raise SystemExit(
            f"missing baseline cell: {case}"
        )

    print("OK:", case)

print()
print("Baseline: 5/5 valid")
PY_BASE


# ============================================================
# Existing benchmark runner adapter
# ============================================================

benchmark_profile() {
  local profile="$1"
  local output="$2"
  local execute="$3"

  docker compose exec -T api \
    python - \
    "$TMP_CORPUS_CONTAINER" \
    "$profile" \
    "$output" \
    "$REFERENCE" \
    "$SEED" \
    "$execute" <<'PY_BENCH'
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

corpus, profile, output, reference, seed, execute = sys.argv[1:]

script = Path("/app/scripts/benchmark_voice_references.py")

spec = importlib.util.spec_from_file_location(
    "reference_benchmark",
    script,
)

if spec is None or spec.loader is None:
    raise SystemExit("cannot import benchmark runner")

module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

module.load_reference_corpus.__defaults__ = (
    Path(corpus),
)

args = [
    "--references-dir",
    "/data/references",
    "--output",
    output,
    "--mode",
    "quick",
    "--refs",
    reference,
    "--generation",
    profile,
    "--seeds",
    seed,
    "--asr-language",
    "pt",
]

if execute == "true":
    args.append("--run")

raise SystemExit(
    module.main(args)
)
PY_BENCH
}


# ============================================================
# Plans
# ============================================================

echo
echo "============================================================"
echo " DRY PLANS"
echo "============================================================"

for profile in "${PROFILES[@]}"; do
  echo
  echo "--- $profile ---"

  benchmark_profile \
    "$profile" \
    "/data/audio/benchmarks/naturalness-temperature-plan/$profile" \
    false
done

if [[ "$RUN" != true ]]; then
  echo
  echo "============================================================"
  echo " PLAN COMPLETE"
  echo "============================================================"
  echo
  echo "No synthesis performed."
  exit 0
fi


# ============================================================
# New run
# ============================================================

RUN_ID="$(
  date -u +%Y%m%dT%H%M%SZ
)-$(
  python3 - <<'PY_ID'
import secrets
print(secrets.token_hex(4))
PY_ID
)"

RUN_ROOT="$NEW_ROOT/$RUN_ID"
PACK_PATH="$PACK_ROOT/$RUN_ID"

echo
echo "============================================================"
echo " RUN ID: $RUN_ID"
echo "============================================================"


# ============================================================
# Compose experimental Qwen
# ============================================================

cat > "$TMP_OVERRIDE_HOST" <<EOF_OVERRIDE
services:
  qwen-tts:
    environment:
      GENERATION_PROFILES_FILE: $TMP_PROFILE_CONTAINER
    volumes:
      - $TMP_PROFILE_HOST:$TMP_PROFILE_CONTAINER:ro
EOF_OVERRIDE

echo
echo "============================================================"
echo " STARTING EXPERIMENTAL QWEN"
echo "============================================================"
echo

cd "$QWEN_DIR"

docker compose \
  --project-name qwen3-tts-workshop \
  -f "$QWEN_BASE" \
  -f "$QWEN_INFRA" \
  -f "$TMP_OVERRIDE_HOST" \
  up -d \
  --force-recreate \
  --no-deps \
  qwen-tts

QWEN_EXPERIMENTAL=true

wait_host_qwen 180 || {
  echo "ERROR: experimental Qwen never became ready"
  exit 1
}

echo "Networks:"

docker inspect qwen3-tts \
  --format '{{range $name,$v := .NetworkSettings.Networks}}{{println "  " $name}}{{end}}'

docker inspect qwen3-tts \
  --format '{{range $name,$v := .NetworkSettings.Networks}}{{println $name}}{{end}}' \
  | grep -qx neovids || {
    echo "ERROR: experimental Qwen lost neovids network"
    exit 1
  }

echo
echo "Orchestrator -> experimental Qwen:"

orchestrator_qwen_check


# ============================================================
# Verify profiles through Qwen
# ============================================================

PROFILE_JSON="/tmp/neovids-profile-check-$TOKEN.json"

curl -fsS \
  http://localhost:8889/v1/generation-profiles \
  -o "$PROFILE_JSON"

python3 - \
  "$PROFILE_JSON" \
  "$BASELINE_PROFILE" \
  "${PROFILES[@]}" <<'PY_VERIFY'
import json
import sys
from pathlib import Path

data = json.loads(
    Path(sys.argv[1]).read_text(
        encoding="utf-8"
    )
)

wanted = set(sys.argv[2:])

if isinstance(data, dict):
    rows = (
        data.get("profiles")
        or data.get("generation_profiles")
        or []
    )
else:
    rows = data

names = {
    row.get("name")
    for row in rows
    if isinstance(row, dict)
}

missing = wanted - names

if missing:
    raise SystemExit(
        f"profiles missing from Qwen: {sorted(missing)}"
    )

print("Profile registry: PASS")
PY_VERIFY

rm -f "$PROFILE_JSON"


# ============================================================
# Run 4 × 5
# ============================================================

declare -A CHILD_RUNS

validate_child() {
  local run="$1"
  local profile="$2"

  docker compose exec -T api \
    python - \
    "$run" \
    "$profile" <<'PY_GATE'
import json
import sys
from pathlib import Path

run = Path(sys.argv[1])
profile = sys.argv[2]

manifest = run / "manifest.jsonl"

rows = [
    json.loads(line)
    for line in manifest
        .read_text(encoding="utf-8")
        .splitlines()
    if line.strip()
]

success = [
    row
    for row in rows
    if row.get("generation_status") == "success"
    and row.get("artifact_integrity_status") == "valid"
]

failed = [
    row
    for row in rows
    if not (
        row.get("generation_status") == "success"
        and row.get("artifact_integrity_status") == "valid"
    )
]

print(
    f"profile={profile} "
    f"successful={len(success)} "
    f"failed={len(failed)}"
)

if len(success) != 5 or failed:
    for row in failed:
        print(
            "FAILED:",
            row.get("case_id"),
            row.get("failure_category"),
            row.get("failure_reason"),
        )

    raise SystemExit(
        f"{profile}: expected 5/5 successful"
    )

print("PROFILE GATE: PASS")
PY_GATE
}


cd "$ORCH_DIR"

for profile in "${PROFILES[@]}"; do
  echo
  echo "============================================================"
  echo " PROFILE: $profile"
  echo "============================================================"
  echo

  PROFILE_ROOT="$RUN_ROOT/$profile"

  benchmark_profile \
    "$profile" \
    "$PROFILE_ROOT" \
    true

  CHILD="$(
    docker compose exec -T api \
      python - "$PROFILE_ROOT" <<'PY_FIND'
from pathlib import Path
import sys

root = Path(sys.argv[1])

children = sorted(
    p
    for p in root.iterdir()
    if p.is_dir()
)

if len(children) != 1:
    raise SystemExit(
        f"expected exactly one child run, found {len(children)}"
    )

print(children[0])
PY_FIND
  )"

  CHILD_RUNS["$profile"]="$CHILD"

  validate_child \
    "$CHILD" \
    "$profile"
done


# ============================================================
# Restore production Qwen using OFFICIAL infra topology
# ============================================================

echo
echo "============================================================"
echo " RESTORING PRODUCTION QWEN"
echo "============================================================"
echo

cd "$QWEN_DIR"

docker compose \
  --project-name qwen3-tts-workshop \
  -f "$QWEN_BASE" \
  -f "$QWEN_INFRA" \
  up -d \
  --force-recreate \
  --no-deps \
  qwen-tts

QWEN_EXPERIMENTAL=false

wait_host_qwen 180 || {
  echo "ERROR: production Qwen failed readiness"
  exit 1
}

orchestrator_qwen_check


# ============================================================
# Build 25-WAV blind pack
# ============================================================

echo
echo "============================================================"
echo " BUILDING BLIND PACK"
echo "============================================================"
echo

cd "$ORCH_DIR"

docker compose exec -T api \
  python - \
  "$PACK_PATH" \
  "$RUN_ID" \
  "$BASELINE_RUN" \
  "${CHILD_RUNS[$PROFILE_MAIN095]}" \
  "${CHILD_RUNS[$PROFILE_MAIN100]}" \
  "${CHILD_RUNS[$PROFILE_SUB095]}" \
  "${CHILD_RUNS[$PROFILE_SUB100]}" \
  "$REFERENCE" \
  "$REFERENCE_SHA256" \
  "$SEED" \
  "$BASELINE_PROFILE" \
  "$PROFILE_MAIN095" \
  "$PROFILE_MAIN100" \
  "$PROFILE_SUB095" \
  "$PROFILE_SUB100" \
  "${CASES[@]}" <<'PY_PACK'
from __future__ import annotations

import csv
import hashlib
import json
import os
import random
import shutil
import sys
from pathlib import Path

(
    pack_raw,
    run_id,
    baseline_raw,
    main095_raw,
    main100_raw,
    sub095_raw,
    sub100_raw,
    reference,
    ref_sha,
    seed_raw,
    baseline,
    main095,
    main100,
    sub095,
    sub100,
    *cases,
) = sys.argv[1:]

pack = Path(pack_raw)
seed = int(seed_raw)

sources = {
    baseline: Path(baseline_raw),
    main095: Path(main095_raw),
    main100: Path(main100_raw),
    sub095: Path(sub095_raw),
    sub100: Path(sub100_raw),
}

params = {
    baseline: (0.90, 0.90, "baseline"),
    main095: (0.95, 0.90, "main"),
    main100: (1.00, 0.90, "main"),
    sub095: (0.90, 0.95, "subtalker"),
    sub100: (0.90, 1.00, "subtalker"),
}

def sha(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as f:
        for chunk in iter(
            lambda: f.read(1024 * 1024),
            b"",
        ):
            h.update(chunk)

    return h.hexdigest()

def manifest(run: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (run / "manifest.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        if line.strip()
    ]

selected = []

for profile, source in sources.items():
    rows = manifest(source)

    for case in cases:
        found = []

        for row in rows:
            if row.get("reference") != reference:
                continue
            if row.get("reference_sha256") != ref_sha:
                continue
            if int(row.get("requested_seed", -1)) != seed:
                continue
            if row.get("case_id") != case:
                continue
            if row.get("generation_profile") != profile:
                continue
            if row.get("clone_mode") != "x_vector_only":
                continue
            if row.get("generation_status") != "success":
                continue
            if row.get("artifact_integrity_status") != "valid":
                continue

            wav = row.get("wav")

            if wav and (source / wav).is_file():
                found.append(
                    (row, source / wav)
                )

        if not found:
            raise SystemExit(
                f"missing cell: {profile} / {case}"
            )

        hashes = {
            sha(path)
            for _, path in found
        }

        if len(hashes) != 1:
            raise SystemExit(
                f"ambiguous WAV: {profile} / {case}"
            )

        row, path = found[-1]

        selected.append(
            {
                "profile": profile,
                "case": case,
                "row": row,
                "path": path,
                "sha": sha(path),
            }
        )

if len(selected) != 25:
    raise SystemExit(
        f"expected 25 cells, got {len(selected)}"
    )

# Verify frozen inputs.
for field in (
    "model_id",
    "model_revision",
    "reference_sha256",
    "reference_identity",
    "clone_mode",
    "chunking_identity",
    "chunking_strategy_version",
    "lexicon_identity",
):
    values = {
        str(item["row"].get(field))
        for item in selected
    }

    if len(values) != 1:
        raise SystemExit(
            f"invariant mismatch {field}: {values}"
        )

for case in cases:
    case_rows = [
        x["row"]
        for x in selected
        if x["case"] == case
    ]

    canonical = {
        x.get("canonical_text")
        for x in case_rows
    }

    synthesis = {
        x.get("actual_synthesis_text")
        for x in case_rows
    }

    if len(canonical) != 1:
        raise SystemExit(
            f"canonical text mismatch: {case}"
        )

    if len(synthesis) != 1:
        raise SystemExit(
            f"synthesis text mismatch: {case}"
        )

if pack.exists():
    shutil.rmtree(pack)

blind = pack / "blind"
private = blind / "private"

private.mkdir(
    parents=True,
    exist_ok=True,
)

profiles = tuple(sources)

labels = list("ABCDE")

rng = random.Random(
    int.from_bytes(
        hashlib.sha256(
            (
                run_id
                + "|naturalness-temp-v2"
            ).encode()
        ).digest()[:8],
        "big",
    )
)

rng.shuffle(labels)

mapping = dict(
    zip(
        profiles,
        labels,
        strict=True,
    )
)

with (
    private / "profile_map_PRIVATE.csv"
).open(
    "w",
    newline="",
    encoding="utf-8",
) as f:
    writer = csv.writer(f)

    writer.writerow(
        [
            "blind_profile",
            "profile",
            "temperature",
            "subtalker_temperature",
            "axis",
        ]
    )

    for profile in profiles:
        main_temp, sub_temp, axis = params[profile]

        writer.writerow(
            [
                mapping[profile],
                profile,
                main_temp,
                sub_temp,
                axis,
            ]
        )

listening = []
private_manifest = []

for item in selected:
    profile = item["profile"]
    case = item["case"]
    label = mapping[profile]

    case_dir = blind / case
    case_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    destination = case_dir / f"{label}.wav"

    try:
        os.link(
            item["path"],
            destination,
        )
    except OSError:
        shutil.copy2(
            item["path"],
            destination,
        )

    if sha(destination) != item["sha"]:
        raise SystemExit(
            f"copy checksum mismatch: {destination}"
        )

    listening.append(
        {
            "case": case,
            "blind_profile": label,
            "wav": str(
                destination.relative_to(pack)
            ),
            "naturalness_1_5": "",
            "rhythm_prosody_1_5": "",
            "pace_1_5": "",
            "speaker_identity_1_5": "",
            "ptbr_accent_1_5": "",
            "pronunciation_1_5": "",
            "overall_1_5": "",
            "catastrophic_failure": "",
            "preferred": "",
            "notes": "",
        }
    )

    private_manifest.append(
        {
            **item["row"],
            "_blind_profile": label,
            "_source_path": str(item["path"]),
        }
    )

fields = [
    "case",
    "play_order",
    "blind_profile",
    "wav",
    "naturalness_1_5",
    "rhythm_prosody_1_5",
    "pace_1_5",
    "speaker_identity_1_5",
    "ptbr_accent_1_5",
    "pronunciation_1_5",
    "overall_1_5",
    "catastrophic_failure",
    "preferred",
    "notes",
]

ordered = []

for case in cases:
    group = [
        row
        for row in listening
        if row["case"] == case
    ]

    case_rng = random.Random(
        int.from_bytes(
            hashlib.sha256(
                (
                    run_id
                    + "|"
                    + case
                ).encode()
            ).digest()[:8],
            "big",
        )
    )

    case_rng.shuffle(group)

    for i, row in enumerate(
        group,
        start=1,
    ):
        row["play_order"] = i
        ordered.append(row)

with (
    blind / "listening.csv"
).open(
    "w",
    newline="",
    encoding="utf-8",
) as f:
    writer = csv.DictWriter(
        f,
        fieldnames=fields,
    )

    writer.writeheader()
    writer.writerows(ordered)

with (
    private / "combined_manifest_PRIVATE.jsonl"
).open(
    "w",
    encoding="utf-8",
) as f:
    for row in private_manifest:
        f.write(
            json.dumps(
                row,
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
        )

(blind / "README.txt").write_text(
    """NeoVids Naturalness / Rhythm Benchmark

DO NOT OPEN private/ BEFORE LISTENING.

All profiles use:
- ref_full_processed_raw.wav
- x_vector_only
- seed 303
- same model
- same text
- same chunking
- same pronunciation preprocessing

Only temperature axis differs.

Evaluate by case, not by profile.

Priority:
1. naturalness
2. rhythm/prosody
3. pace
4. speaker identity stability
5. PT-BR accent
6. pronunciation

Any wrong speaker / female voice / strong PT-PT drift /
runaway / major content loss is catastrophic.
""",
    encoding="utf-8",
)

wavs = list(
    blind.glob("*/*.wav")
)

if len(wavs) != 25:
    raise SystemExit(
        f"expected 25 WAVs, got {len(wavs)}"
    )

print()
print("BLIND PACK COMPLETE")
print("blind_wavs=25")
print(
    "listening=",
    blind / "listening.csv",
)
print(
    "private_map=",
    private / "profile_map_PRIVATE.csv",
)
PY_PACK


# ============================================================
# Export
# ============================================================

mkdir -p "$EXPORT_DIR"

ARCHIVE="${RUN_ID}-naturalness-temperature.tar.gz"

docker compose exec -T api sh -lc "
  cd '$(dirname "$PACK_PATH")' &&
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
echo "20/20 new generations expected successful."
echo "25 blind WAVs."
echo
echo "Archive:"
echo "  $EXPORT_DIR/$ARCHIVE"
echo
echo "Listening:"
echo "  $PACK_PATH/blind/listening.csv"
echo
echo "DO NOT OPEN YET:"
echo "  $PACK_PATH/blind/private/profile_map_PRIVATE.csv"

