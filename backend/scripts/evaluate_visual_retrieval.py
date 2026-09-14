#!/usr/bin/env python3
"""Run the read-only visual retrieval evaluation harness or summarize human labels."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from app.core.config import settings
from app.services.visual_retrieval_evaluation import (
    SUMMARY_SCHEMA_VERSION,
    VisualRetrievalEvaluator,
    comparison,
    labeled_metrics,
    load_dataset,
    load_report,
    parse_review_labels,
    render_markdown,
    render_review_csv,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="execute read-only raw retrieval evaluation")
    source = run.add_mutually_exclusive_group(required=True)
    source.add_argument("--dataset", type=Path)
    source.add_argument("--roteiro-id", type=UUID)
    source.add_argument("--workflow-id", type=UUID)
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--media-indexer-url", default=settings.media_indexer_api_url)
    run.add_argument("--ideas-hub-url", default=settings.ideas_hub_api_url)
    run.add_argument("--orchestrator-url", default="http://localhost:8011")

    summary = subparsers.add_parser("summary", help="aggregate a completed review manifest")
    summary.add_argument("--report", type=Path, required=True)
    summary.add_argument("--labels", type=Path, required=True)
    summary.add_argument("--output", type=Path, required=True)

    compare = subparsers.add_parser("compare", help="compare reports with the same dataset")
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--candidate", type=Path, required=True)
    compare.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


async def run_evaluation(args: argparse.Namespace) -> int:
    config = settings.model_copy(
        update={
            "media_indexer_api_url": args.media_indexer_url,
            "ideas_hub_api_url": args.ideas_hub_url,
        }
    )
    evaluator = VisualRetrievalEvaluator(config, orchestrator_url=args.orchestrator_url)
    plan = None
    if args.dataset:
        dataset = load_dataset(args.dataset.resolve(strict=True))
        mode: Literal["dataset", "roteiro", "workflow"] = "dataset"
        input_identity = str(args.dataset.resolve())
    elif args.roteiro_id:
        dataset, plan = await evaluator.load_roteiro(args.roteiro_id)
        mode = "roteiro"
        input_identity = str(args.roteiro_id)
    else:
        dataset, plan = await evaluator.load_workflow(args.workflow_id)
        mode = "workflow"
        input_identity = str(args.workflow_id)
    report = await evaluator.evaluate(
        dataset, mode=mode, input_identity=input_identity, plan=plan
    )
    output = args.output_dir.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("output directory must be empty or absent")
    output.mkdir(parents=True, exist_ok=True)
    _write(
        output / "report.json",
        report.model_dump_json(indent=2, exclude_none=False) + "\n",
    )
    _write(output / "report.md", render_markdown(report))
    _write(output / "review.csv", render_review_csv(report))
    print(f"report={output / 'report.json'}")
    print(f"markdown={output / 'report.md'}")
    print(f"review={output / 'review.csv'}")
    print(f"queries={report.metrics['total_queries']}")
    print(f"temporal_scope_leakage={report.metrics['temporal_scope_leakage_count']}")
    return 2 if report.violations else 0


def summarize(args: argparse.Namespace) -> int:
    report = load_report(args.report)
    rows = parse_review_labels(args.labels, report)
    payload: dict[str, Any] = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "report_identity": report.report_identity,
        "dataset_identity": report.dataset_identity,
        "objective_metrics": report.metrics,
        "human_label_metrics": labeled_metrics(report, rows),
    }
    _write(args.output.resolve(), json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(f"summary={args.output.resolve()}")
    human_metrics = payload["human_label_metrics"]
    assert isinstance(human_metrics, dict)
    print(f"labeled_queries={human_metrics['labeled_queries']}")
    return 0


def compare_reports(args: argparse.Namespace) -> int:
    payload = comparison(load_report(args.baseline), load_report(args.candidate))
    _write(args.output.resolve(), json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(f"comparison={args.output.resolve()}")
    return 0


def main() -> int:
    args = parse_args()
    if args.command == "run":
        return asyncio.run(run_evaluation(args))
    if args.command == "summary":
        return summarize(args)
    return compare_reports(args)


if __name__ == "__main__":
    raise SystemExit(main())
