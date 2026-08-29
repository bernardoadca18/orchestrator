#!/usr/bin/env bash
set -Eeuo pipefail

# ============================================================
# NeoVids - Final Seed Blind Benchmark
#
# PURPOSE
#
# Final human comparison of the four surviving seed candidates
# using ONLY already-generated, physically validated WAVs.
#
# NO Qwen synthesis.
# NO CUDA inference.
# NO ASR.
# NO production mutation.
#
# Matrix:
#
#   4 seeds
#   x 6 strategic cases
#   = 24 historical WAVs
#
# Seeds are hidden behind stable labels A-D.
# The same label represents the same seed in every case.
#
# Default behavior is PLAN ONLY.
# Pass --run to actually build/export the blind pack.
# ============================================================


# ------------------------------------------------------------
# Paths
# ------------------------------------------------------------

ORCH_DIR="${ORCH_DIR:-$HOME/workspaces/neovids/orchestrator}"

OUTPUT_ROOT="${OUTPUT_ROOT:-/data/audio/benchmarks/seed-final-blind}"

EXPORT_DIR="${EXPORT_DIR:-$ORCH_DIR/benchmark_exports}"

REFERENCE="ref_full_processed_raw.wav"

EXPECTED_REFERENCE_SHA256="8856924e05f76d1a3a5b8554218f80b5e1a898aebfe211ec1b65b60eabdbc70b"

GENERATION_PROFILE="narration_v1"

CLONE_MODE="x_vector_only"


# ------------------------------------------------------------
# Historical source runs
# ------------------------------------------------------------
#
# 61 / 101
#   Seed Robustness Sweep
#
# 303
#   Finalist reference benchmark
#
# 424242
#   Auditory reference benchmark
# ------------------------------------------------------------

SEED_SWEEP_RUN="/data/audio/benchmarks/seed-robustness/20260829T154314Z-ce5f478e"

FINALISTS_RUN="/data/audio/benchmarks/reference-regression/20260829T035529Z-35a384d7"

AUDITORY_RUN="/data/audio/benchmarks/reference-regression/20260829T020355Z-8cc499e1"


# ------------------------------------------------------------
# Matrix
# ------------------------------------------------------------

SEEDS=(
  61
  101
  303
  424242
)

CASES=(
  natural_ptbr
  gameplay_narration
  controlled_intensity
  content_skip_canary
  mixed_english
  long_form
)

EXPECTED_SEEDS="${#SEEDS[@]}"
EXPECTED_CASES="${#CASES[@]}"
EXPECTED_WAVS="$((EXPECTED_SEEDS * EXPECTED_CASES))"


# ------------------------------------------------------------
# CLI
# ------------------------------------------------------------

RUN=false

usage() {
  cat <<USAGE
Usage:

  $0
      Validate and show the plan only.
      Writes nothing and performs no synthesis.

  $0 --run
      Build the 24-WAV blind pack and export a tar.gz.

Examples:

  ./run_seed_final_blind.sh

  ./run_seed_final_blind.sh --run
USAGE
}


while [[ $# -gt 0 ]]; do
  case "$1" in

    --run)
      RUN=true
      shift
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


# ============================================================
# Basic preflight
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
  exit 1
fi


echo
echo "============================================================"
echo " NeoVids - Final Seed Blind Benchmark"
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
echo "Candidate seeds:"
for seed in "${SEEDS[@]}"; do
  echo "  - $seed"
done

echo
echo "Strategic cases:"
for case_id in "${CASES[@]}"; do
  echo "  - $case_id"
done

echo
echo "Matrix:"
echo "  seeds: $EXPECTED_SEEDS"
echo "  cases: $EXPECTED_CASES"
echo "  WAVs:  $EXPECTED_WAVS"

echo
echo "GPU generations:"
echo "  0"


# ============================================================
# Historical evidence preflight
# ============================================================

echo
echo "============================================================"
echo " HISTORICAL EVIDENCE PREFLIGHT"
echo "============================================================"
echo


docker compose exec -T api \
  python - \
  "$REFERENCE" \
  "$EXPECTED_REFERENCE_SHA256" \
  "$GENERATION_PROFILE" \
  "$CLONE_MODE" \
  "$SEED_SWEEP_RUN" \
  "$FINALISTS_RUN" \
  "$AUDITORY_RUN" \
  "${CASES[@]}" <<'PY'
from __future__ import annotations

import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path


reference = sys.argv[1]
expected_reference_sha = sys.argv[2]
generation_profile = sys.argv[3]
clone_mode = sys.argv[4]

seed_sweep = Path(sys.argv[5])
finalists = Path(sys.argv[6])
auditory = Path(sys.argv[7])

cases = tuple(sys.argv[8:])

seed_sources = {
    61: seed_sweep,
    101: seed_sweep,
    303: finalists,
    424242: auditory,
}

expected_total = len(seed_sources) * len(cases)


def load_manifest(run: Path) -> list[dict]:
    manifest = run / "manifest.jsonl"

    if not run.is_dir():
        raise SystemExit(
            f"historical run directory missing: {run}"
        )

    if not manifest.is_file():
        raise SystemExit(
            f"historical manifest missing: {manifest}"
        )

    rows = []

    with manifest.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue

            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(
                    f"invalid JSON in {manifest}:{line_number}"
                ) from exc

            rows.append(row)

    return rows


manifest_cache = {
    run: load_manifest(run)
    for run in set(seed_sources.values())
}


def case_id(row: dict) -> str | None:
    value = row.get("case_id")

    if value is None:
        value = row.get("case")

    return str(value) if value is not None else None


def valid_candidate(row: dict, *, seed: int, wanted_case: str) -> bool:
    if int(row.get("requested_seed", -1)) != seed:
        return False

    if case_id(row) != wanted_case:
        return False

    if row.get("reference") != reference:
        return False

    if row.get("generation_status") != "success":
        return False

    if row.get("artifact_integrity_status") != "valid":
        return False

    wav = row.get("wav")

    if not isinstance(wav, str) or not wav:
        return False

    return True


selected: dict[tuple[int, str], tuple[dict, Path, Path]] = {}


for seed, run in seed_sources.items():

    rows = manifest_cache[run]

    for wanted_case in cases:

        matches = [
            row
            for row in rows
            if valid_candidate(
                row,
                seed=seed,
                wanted_case=wanted_case,
            )
        ]

        if not matches:
            raise SystemExit(
                "missing valid historical WAV: "
                f"seed={seed} case={wanted_case} run={run}"
            )

        existing = []

        for row in matches:
            wav_path = run / row["wav"]

            if wav_path.is_file():
                existing.append((row, wav_path))

        if not existing:
            raise SystemExit(
                "manifest records exist but WAV is missing: "
                f"seed={seed} case={wanted_case} run={run}"
            )

        # If a manifest contains multiple valid rows for the same
        # seed/case, they are acceptable only if the actual WAV
        # checksum is identical.
        observed_shas = set()

        for _, wav_path in existing:
            digest = hashlib.sha256()

            with wav_path.open("rb") as handle:
                for block in iter(
                    lambda: handle.read(1024 * 1024),
                    b"",
                ):
                    digest.update(block)

            observed_shas.add(digest.hexdigest())

        if len(observed_shas) != 1:
            raise SystemExit(
                "ambiguous historical cell with multiple WAVs: "
                f"seed={seed} case={wanted_case}"
            )

        row, wav_path = existing[-1]

        selected[(seed, wanted_case)] = (
            row,
            wav_path,
            run,
        )

        print(
            f"OK seed={seed:<6} "
            f"case={wanted_case:<22} "
            f"run={run.name}"
        )


if len(selected) != expected_total:
    raise SystemExit(
        f"expected {expected_total} cells, got {len(selected)}"
    )


print()
print("Historical cells:")
print(f"  {len(selected)}/{expected_total} valid")


# ============================================================
# Cross-run invariants
# ============================================================

rows = [
    item[0]
    for item in selected.values()
]


def unique_present(field: str) -> set[str]:
    values = set()

    for row in rows:
        value = row.get(field)

        if value is not None and str(value).strip():
            values.add(str(value))

    return values


reference_shas = unique_present("reference_sha256")

if reference_shas != {expected_reference_sha}:
    raise SystemExit(
        "reference SHA mismatch across selected cells: "
        f"{sorted(reference_shas)}"
    )


clone_modes = unique_present("clone_mode")

if clone_modes != {clone_mode}:
    raise SystemExit(
        "clone mode mismatch: "
        f"{sorted(clone_modes)}"
    )


generation_profiles = unique_present(
    "generation_profile"
)

if generation_profiles != {generation_profile}:
    raise SystemExit(
        "generation profile mismatch: "
        f"{sorted(generation_profiles)}"
    )


# These fields are required to agree whenever exposed.
optional_global_invariants = (
    "model_id",
    "model_revision",
    "generation_identity",
    "generation_profile_identity",
    "reference_identity",
    "chunking_identity",
    "chunking_strategy_version",
    "lexicon_identity",
)

print()
print("Global invariants:")

print(
    f"  reference_sha256: {expected_reference_sha}"
)
print(
    f"  clone_mode:       {clone_mode}"
)
print(
    f"  generation:       {generation_profile}"
)


for field in optional_global_invariants:

    values = unique_present(field)

    if len(values) > 1:
        raise SystemExit(
            f"cross-run invariant mismatch for {field}: "
            f"{sorted(values)}"
        )

    if values:
        print(
            f"  {field}: "
            f"{next(iter(values))}"
        )
    else:
        print(
            f"  {field}: <not exposed>"
        )


# ============================================================
# Case-level text invariants
# ============================================================

print()
print("Case text invariants:")


for wanted_case in cases:

    case_rows = [
        selected[(seed, wanted_case)][0]
        for seed in seed_sources
    ]

    canonical_values = {
        str(row.get("canonical_text"))
        for row in case_rows
        if row.get("canonical_text") is not None
    }

    if len(canonical_values) > 1:
        raise SystemExit(
            "canonical text differs across seeds for "
            f"case={wanted_case}"
        )

    synthesis_values = {
        str(row.get("synthesis_text"))
        for row in case_rows
        if row.get("synthesis_text") is not None
    }

    if len(synthesis_values) > 1:
        raise SystemExit(
            "synthesis text differs across seeds for "
            f"case={wanted_case}"
        )

    print(
        f"  {wanted_case}: PASS"
    )


# ============================================================
# Current reference check
# ============================================================

current_reference = (
    Path("/data/references")
    / reference
)

if current_reference.is_file():

    digest = hashlib.sha256()

    with current_reference.open("rb") as handle:
        for block in iter(
            lambda: handle.read(1024 * 1024),
            b"",
        ):
            digest.update(block)

    current_sha = digest.hexdigest()

    if current_sha != expected_reference_sha:
        raise SystemExit(
            "current mounted reference differs from historical "
            f"winner: {current_sha}"
        )

    print()
    print("Current reference:")
    print(f"  SHA256: {current_sha}")
    print("  status: PASS")

else:

    print()
    print(
        "Current reference mount not available; "
        "historical evidence remains self-consistent."
    )


print()
print("FINAL PREFLIGHT: PASS")
print()
PY


if [[ "$RUN" != true ]]; then

  echo "============================================================"
  echo " PLAN COMPLETE"
  echo "============================================================"
  echo
  echo "No files were created."
  echo "No Qwen request was made."
  echo "No CUDA inference was performed."
  echo
  echo "To create the blind pack:"
  echo
  echo "  ./run_seed_final_blind.sh --run"
  echo

  exit 0

fi


# ============================================================
# Create fresh run ID
# ============================================================

RANDOM_SUFFIX="$(
  python3 - <<'PY'
import secrets
print(secrets.token_hex(4))
PY
)"

RUN_ID="$(
  date -u +%Y%m%dT%H%M%SZ
)-${RANDOM_SUFFIX}"

RUN_PATH="$OUTPUT_ROOT/$RUN_ID"


echo
echo "============================================================"
echo " BUILDING BLIND PACK"
echo "============================================================"
echo

echo "Run ID:"
echo "  $RUN_ID"

echo
echo "Container path:"
echo "  $RUN_PATH"

echo


# ============================================================
# Build pack inside API container
# ============================================================

docker compose exec -T api \
  python - \
  "$RUN_PATH" \
  "$RUN_ID" \
  "$REFERENCE" \
  "$EXPECTED_REFERENCE_SHA256" \
  "$GENERATION_PROFILE" \
  "$CLONE_MODE" \
  "$SEED_SWEEP_RUN" \
  "$FINALISTS_RUN" \
  "$AUDITORY_RUN" \
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


# ============================================================
# Arguments
# ============================================================

run_dir = Path(sys.argv[1])

run_id = sys.argv[2]

reference = sys.argv[3]

expected_reference_sha = sys.argv[4]

generation_profile = sys.argv[5]

clone_mode = sys.argv[6]

seed_sweep = Path(sys.argv[7])

finalists = Path(sys.argv[8])

auditory = Path(sys.argv[9])

cases = tuple(sys.argv[10:])


seed_sources = {
    61: seed_sweep,
    101: seed_sweep,
    303: finalists,
    424242: auditory,
}

seeds = tuple(seed_sources)

expected_total = (
    len(seeds)
    * len(cases)
)


# ============================================================
# Helpers
# ============================================================

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for block in iter(
            lambda: handle.read(1024 * 1024),
            b"",
        ):
            digest.update(block)

    return digest.hexdigest()


def load_manifest(run: Path) -> list[dict]:
    rows = []

    with (
        run / "manifest.jsonl"
    ).open(
        "r",
        encoding="utf-8",
    ) as handle:

        for line in handle:

            if line.strip():

                rows.append(
                    json.loads(line)
                )

    return rows


def get_case(row: dict) -> str | None:
    value = row.get("case_id")

    if value is None:
        value = row.get("case")

    return (
        str(value)
        if value is not None
        else None
    )


manifest_cache = {
    run: load_manifest(run)
    for run in set(
        seed_sources.values()
    )
}


# ============================================================
# Select exact historical cells
# ============================================================

selected = []


for seed, source_run in seed_sources.items():

    for wanted_case in cases:

        valid = []

        for row in manifest_cache[source_run]:

            if int(
                row.get(
                    "requested_seed",
                    -1,
                )
            ) != seed:
                continue

            if get_case(row) != wanted_case:
                continue

            if (
                row.get("reference")
                != reference
            ):
                continue

            if (
                row.get(
                    "reference_sha256"
                )
                != expected_reference_sha
            ):
                continue

            if (
                row.get(
                    "generation_status"
                )
                != "success"
            ):
                continue

            if (
                row.get(
                    "artifact_integrity_status"
                )
                != "valid"
            ):
                continue

            if (
                row.get(
                    "clone_mode"
                )
                != clone_mode
            ):
                continue

            if (
                row.get(
                    "generation_profile"
                )
                != generation_profile
            ):
                continue

            wav = row.get("wav")

            if not wav:
                continue

            source_wav = (
                source_run
                / wav
            )

            if not source_wav.is_file():
                continue

            valid.append(
                (
                    row,
                    source_wav,
                )
            )


        if not valid:

            raise SystemExit(
                "historical cell disappeared after preflight: "
                f"seed={seed} "
                f"case={wanted_case}"
            )


        # Last valid entry is used only after proving all
        # existing valid entries contain the same bytes.
        hashes = {
            sha256_file(path)
            for _, path in valid
        }

        if len(hashes) != 1:

            raise SystemExit(
                "multiple different historical artifacts found: "
                f"seed={seed} case={wanted_case}"
            )


        row, source_wav = valid[-1]


        selected.append(
            {
                "seed": seed,
                "case": wanted_case,
                "source_run":
                    source_run,
                "source_wav":
                    source_wav,
                "artifact_sha256":
                    sha256_file(
                        source_wav
                    ),
                "record":
                    row,
            }
        )


if len(selected) != expected_total:

    raise SystemExit(
        f"expected {expected_total} "
        f"selected cells; "
        f"got {len(selected)}"
    )


# ============================================================
# Revalidate cross-run invariants
# ============================================================

records = [
    item["record"]
    for item in selected
]


def unique_present(field: str):
    return {
        str(row[field])
        for row in records
        if row.get(field) is not None
        and str(row[field]).strip()
    }


if unique_present(
    "reference_sha256"
) != {
    expected_reference_sha
}:
    raise SystemExit(
        "reference invariant failed"
    )


if unique_present(
    "clone_mode"
) != {
    clone_mode
}:
    raise SystemExit(
        "clone-mode invariant failed"
    )


if unique_present(
    "generation_profile"
) != {
    generation_profile
}:
    raise SystemExit(
        "generation-profile invariant failed"
    )


for field in (
    "model_id",
    "model_revision",
    "generation_identity",
    "generation_profile_identity",
    "reference_identity",
    "chunking_identity",
    "chunking_strategy_version",
    "lexicon_identity",
):

    values = unique_present(
        field
    )

    if len(values) > 1:

        raise SystemExit(
            "cross-run invariant failed: "
            f"{field}={sorted(values)}"
        )


for wanted_case in cases:

    rows = [
        item["record"]
        for item in selected
        if item["case"]
        == wanted_case
    ]


    canonical = {
        str(row["canonical_text"])
        for row in rows
        if row.get(
            "canonical_text"
        ) is not None
    }

    if len(canonical) > 1:

        raise SystemExit(
            "canonical-text invariant failed: "
            f"{wanted_case}"
        )


    synthesis = {
        str(row["synthesis_text"])
        for row in rows
        if row.get(
            "synthesis_text"
        ) is not None
    }

    if len(synthesis) > 1:

        raise SystemExit(
            "synthesis-text invariant failed: "
            f"{wanted_case}"
        )


# ============================================================
# Create output directories
# ============================================================

if run_dir.exists():

    raise SystemExit(
        f"refusing to overwrite existing run: {run_dir}"
    )


blind_dir = (
    run_dir
    / "blind"
)

private_dir = (
    blind_dir
    / "private"
)

blind_dir.mkdir(
    parents=True
)

private_dir.mkdir(
    parents=True
)


# ============================================================
# Seed → blind label mapping
# ============================================================

labels = [
    chr(
        ord("A") + index
    )
    for index in range(
        len(seeds)
    )
]


mapping_rng = random.Random(
    int.from_bytes(
        hashlib.sha256(
            (
                run_id
                + "|final-seed-blind-map-v1"
            ).encode(
                "utf-8"
            )
        ).digest()[:8],
        "big",
    )
)

mapping_rng.shuffle(
    labels
)


seed_to_label = dict(
    zip(
        seeds,
        labels,
        strict=True,
    )
)


# ============================================================
# Private map
# ============================================================

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

    for seed in seeds:

        writer.writerow(
            [
                seed_to_label[
                    seed
                ],
                seed,
            ]
        )


# ============================================================
# Copy/hardlink WAVs
# ============================================================

listening_rows = []

private_records = []


for item in selected:

    seed = int(
        item["seed"]
    )

    wanted_case = str(
        item["case"]
    )

    label = seed_to_label[
        seed
    ]

    source_wav = Path(
        item["source_wav"]
    )

    destination_dir = (
        blind_dir
        / wanted_case
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
            source_wav,
            destination,
        )

    except OSError:

        shutil.copy2(
            source_wav,
            destination,
        )


    copied_sha = sha256_file(
        destination
    )

    if (
        copied_sha
        != item[
            "artifact_sha256"
        ]
    ):

        raise SystemExit(
            "blind-pack checksum mismatch: "
            f"{destination}"
        )


    row = item["record"]


    canonical_text = (
        row.get(
            "canonical_text"
        )
        or ""
    )


    listening_rows.append(
        {
            "case":
                wanted_case,

            "blind_seed":
                label,

            "wav":
                str(
                    destination.relative_to(
                        run_dir
                    )
                ),

            "canonical_text":
                canonical_text,

            "speaker_identity_stability_1_5":
                "",

            "naturalness_1_5":
                "",

            "pace_1_5":
                "",

            "rhythm_prosody_1_5":
                "",

            "ptbr_accent_1_5":
                "",

            "pronunciation_1_5":
                "",

            "breath_artifacts_1_5":
                "",

            "click_artifacts_1_5":
                "",

            "overall_1_5":
                "",

            "catastrophic_failure":
                "",

            "failure_type":
                "",

            "preferred":
                "",

            "notes":
                "",
        }
    )


    private_records.append(
        {
            **row,

            "_blind_seed":
                label,

            "_source_run":
                str(
                    item[
                        "source_run"
                    ]
                ),

            "_source_wav":
                str(
                    source_wav
                ),

            "_blind_artifact_sha256":
                copied_sha,
        }
    )


# ============================================================
# Randomize play order separately per case
# ============================================================

grouped = defaultdict(list)


for row in listening_rows:

    grouped[
        row["case"]
    ].append(
        row
    )


ordered_rows = []


for wanted_case in cases:

    group = grouped[
        wanted_case
    ]


    local_seed = int.from_bytes(
        hashlib.sha256(
            (
                run_id
                + "|"
                + wanted_case
                + "|final-seed-play-order-v1"
            ).encode(
                "utf-8"
            )
        ).digest()[:8],
        "big",
    )


    local_rng = random.Random(
        local_seed
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

        ordered_rows.append(
            row
        )


# ============================================================
# Public listening worksheet
# ============================================================

fields = [
    "case",
    "play_order",
    "blind_seed",
    "wav",
    "canonical_text",

    "speaker_identity_stability_1_5",
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

    "preferred",
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
        ordered_rows
    )


# ============================================================
# Private combined manifest
# ============================================================

with (
    private_dir
    / "combined_manifest_PRIVATE.jsonl"
).open(
    "w",
    encoding="utf-8",
) as handle:

    for row in private_records:

        handle.write(
            json.dumps(
                row,
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
        )


# ============================================================
# Private source map
# ============================================================

with (
    private_dir
    / "sources_PRIVATE.csv"
).open(
    "w",
    newline="",
    encoding="utf-8",
) as handle:

    fields = [
        "blind_seed",
        "seed",
        "case",
        "source_run",
        "source_wav",
        "artifact_sha256",
        "job_id",
    ]

    writer = csv.DictWriter(
        handle,
        fieldnames=fields,
    )

    writer.writeheader()


    for item in sorted(
        selected,
        key=lambda x: (
            int(x["seed"]),
            str(x["case"]),
        ),
    ):

        row = item["record"]

        writer.writerow(
            {
                "blind_seed":
                    seed_to_label[
                        int(
                            item[
                                "seed"
                            ]
                        )
                    ],

                "seed":
                    item["seed"],

                "case":
                    item["case"],

                "source_run":
                    str(
                        item[
                            "source_run"
                        ]
                    ),

                "source_wav":
                    str(
                        item[
                            "source_wav"
                        ]
                    ),

                "artifact_sha256":
                    item[
                        "artifact_sha256"
                    ],

                "job_id":
                    row.get(
                        "job_id",
                        "",
                    ),
            }
        )


# ============================================================
# Private machine-summary CSV
#
# DO NOT expose during blind listening because machine metrics
# may bias human judgment.
# ============================================================

metric_fields = {
    "normalized_wer":
        "wer",

    "coverage":
        "coverage",

    "physical_duration_seconds":
        "duration",

    "silence_ratio":
        "silence_ratio",

    "pause_count":
        "pause_count",

    "leading_silence_seconds":
        "leading_silence",

    "trailing_silence_seconds":
        "trailing_silence",
}


machine_rows = []


for seed in seeds:

    seed_items = [
        item
        for item in selected
        if int(
            item["seed"]
        ) == seed
    ]


    result = {
        "blind_seed":
            seed_to_label[
                seed
            ],

        "seed":
            seed,

        "case_count":
            len(
                seed_items
            ),
    }


    for output_name, field in [
        (
            "mean_wer",
            "normalized_wer",
        ),
        (
            "mean_coverage",
            "coverage",
        ),
        (
            "mean_duration_seconds",
            "physical_duration_seconds",
        ),
        (
            "mean_silence_ratio",
            "silence_ratio",
        ),
        (
            "mean_pause_count",
            "pause_count",
        ),
        (
            "mean_leading_silence_seconds",
            "leading_silence_seconds",
        ),
        (
            "mean_trailing_silence_seconds",
            "trailing_silence_seconds",
        ),
    ]:

        values = []

        for item in seed_items:

            value = (
                item["record"]
                .get(
                    field
                )
            )

            if isinstance(
                value,
                (
                    int,
                    float,
                ),
            ):
                values.append(
                    float(
                        value
                    )
                )


        result[
            output_name
        ] = (
            round(
                sum(values)
                / len(values),
                6,
            )
            if values
            else ""
        )


    machine_rows.append(
        result
    )


with (
    private_dir
    / "machine_summary_PRIVATE.csv"
).open(
    "w",
    newline="",
    encoding="utf-8",
) as handle:

    fields = [
        "blind_seed",
        "seed",
        "case_count",

        "mean_wer",
        "mean_coverage",

        "mean_duration_seconds",

        "mean_silence_ratio",
        "mean_pause_count",

        "mean_leading_silence_seconds",
        "mean_trailing_silence_seconds",
    ]


    writer = csv.DictWriter(
        handle,
        fieldnames=fields,
    )

    writer.writeheader()

    writer.writerows(
        machine_rows
    )


# ============================================================
# Public metadata
# ============================================================

public_metadata = {
    "schema":
        "neovids_final_seed_blind_v1",

    "run_id":
        run_id,

    "reference":
        reference,

    "reference_sha256":
        expected_reference_sha,

    "clone_mode":
        clone_mode,

    "generation_profile":
        generation_profile,

    "seed_count":
        len(seeds),

    "case_count":
        len(cases),

    "wav_count":
        len(selected),

    "cases":
        list(cases),

    "new_synthesis_count":
        0,

    "historical_reuse_count":
        len(selected),

    "production_promoted":
        False,
}


(
    blind_dir
    / "metadata.json"
).write_text(
    json.dumps(
        public_metadata,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)


# ============================================================
# Private metadata
# ============================================================

private_metadata = {
    **public_metadata,

    "candidate_seeds":
        list(seeds),

    "seed_sources": {
        str(seed):
            str(
                seed_sources[
                    seed
                ]
            )
        for seed in seeds
    },

    "blind_mapping": {
        label:
            seed
        for seed, label
        in seed_to_label.items()
    },
}


(
    private_dir
    / "metadata_PRIVATE.json"
).write_text(
    json.dumps(
        private_metadata,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)


# ============================================================
# Listening guide
# ============================================================

(
    blind_dir
    / "README.txt"
).write_text(
    """NeoVids - FINAL SEED BLIND BENCHMARK

DO NOT OPEN THE private/ DIRECTORY UNTIL LISTENING IS COMPLETE.

This benchmark contains:

    4 candidate seeds
    x
    6 strategic cases
    =
    24 previously-generated WAV files.

NO audio was regenerated for this pack.

The reference, clone mode and generation profile are identical.

The SAME blind letter represents the SAME seed in all six cases.

============================================================
RECOMMENDED LISTENING METHOD
============================================================

Evaluate one CASE at a time.

For example:

    natural_ptbr/A.wav
    natural_ptbr/B.wav
    natural_ptbr/C.wav
    natural_ptbr/D.wav

Then move to gameplay_narration, etc.

Use listening.csv for randomized play_order.

Do not listen to all A files first, then all B files.
That creates unnecessary identity/expectation bias.

============================================================
SCORING
============================================================

5 = excellent
4 = good
3 = acceptable
2 = poor
1 = unusable

Priority:

1. catastrophic stability
2. speaker identity stability
3. naturalness
4. rhythm / prosody
5. pace
6. Brazilian Portuguese accent
7. pronunciation
8. timbre
9. breathing / click artifacts

speaker_identity_stability_1_5:

5 = expected speaker throughout
4 = tiny harmless timbre variation
3 = noticeable but acceptable variation
2 = major identity drift
1 = clearly wrong speaker / wrong voice

For breath_artifacts and click_artifacts:

5 = none
4 = barely perceptible
3 = noticeable but acceptable
2 = distracting
1 = severe

============================================================
CATASTROPHIC FAILURE
============================================================

Set catastrophic_failure=yes for:

- wrong speaker;
- wrong-gender/female voice;
- severe unexpected PT-PT identity/accent drift;
- in-WAV speaker identity change;
- runaway;
- obvious missing content;
- severe repetition;
- corrupt or unusable speech.

Suggested failure_type values:

wrong_speaker
speaker_drift
dialect_drift
runaway
missing_content
severe_repetition
artifact_failure
other

============================================================
HOW TO CHOOSE THE WINNER
============================================================

Do NOT choose the seed that generated the single best WAV.

Choose the seed whose WORST output is still production-quality.

A candidate with one astonishing sample and one catastrophic
sample is worse than a candidate that is consistently excellent.

Pay particular attention to:

    natural_ptbr
    gameplay_narration
    controlled_intensity
    long_form

because naturality, rhythm and speaker stability are currently
more important than tiny ASR/pronunciation differences.

============================================================
PRIVATE FILES
============================================================

Do NOT inspect until listening is complete:

private/seed_map_PRIVATE.csv
private/sources_PRIVATE.csv
private/combined_manifest_PRIVATE.jsonl
private/machine_summary_PRIVATE.csv
private/metadata_PRIVATE.json

After completing your human ranking, the private mapping can be
revealed and machine metrics used as secondary evidence.

Human listening remains authoritative for voice quality.
""",
    encoding="utf-8",
)


# ============================================================
# Final validation
# ============================================================

blind_wavs = list(
    blind_dir.glob(
        "*/*.wav"
    )
)


if len(blind_wavs) != expected_total:

    raise SystemExit(
        f"blind pack expected "
        f"{expected_total} WAVs; "
        f"found {len(blind_wavs)}"
    )


for wav in blind_wavs:

    if not wav.is_file():

        raise SystemExit(
            f"missing blind WAV: {wav}"
        )


worksheet_rows = []

with (
    blind_dir
    / "listening.csv"
).open(
    "r",
    encoding="utf-8",
) as handle:

    worksheet_rows = list(
        csv.DictReader(
            handle
        )
    )


if len(
    worksheet_rows
) != expected_total:

    raise SystemExit(
        "listening.csv row count mismatch"
    )


# Every case must expose four different blind labels.
for wanted_case in cases:

    labels = {
        row["blind_seed"]
        for row in worksheet_rows
        if row["case"]
        == wanted_case
    }

    if len(labels) != len(seeds):

        raise SystemExit(
            "blind label count mismatch: "
            f"{wanted_case}"
        )


print()
print("=" * 68)
print("FINAL BLIND PACK COMPLETE")
print("=" * 68)
print()

print(
    f"run_id={run_id}"
)

print(
    f"historical_wavs={len(selected)}"
)

print(
    "new_generations=0"
)

print(
    f"blind_wavs={len(blind_wavs)}"
)

print(
    f"listening={blind_dir / 'listening.csv'}"
)

print(
    "private_map="
    f"{private_dir / 'seed_map_PRIVATE.csv'}"
)
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

ARCHIVE="${RUN_ID}-final-seed-blind.tar.gz"


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
echo "Historical WAVs:"
echo "  $EXPECTED_WAVS"

echo
echo "New Qwen generations:"
echo "  0"

echo
echo "Host archive:"
echo "  $EXPORT_DIR/$ARCHIVE"

echo
echo "Blind listening worksheet:"
echo "  $RUN_PATH/blind/listening.csv"

echo
echo "DO NOT OPEN UNTIL LISTENING IS COMPLETE:"
echo "  $RUN_PATH/blind/private/seed_map_PRIVATE.csv"

echo
