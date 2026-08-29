#!/usr/bin/env python3
# ruff: noqa: E402
"""Aggregate a filled listening.csv without modifying the machine manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
import sys

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.reference_benchmark import aggregate_human_ratings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("listening_csv", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    destination = args.output or args.listening_csv.with_name("human_ratings_summary.json")
    destination.write_text(
        json.dumps(aggregate_human_ratings(args.listening_csv), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
