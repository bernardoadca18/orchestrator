"""Opt-in contract smoke for the complete NeoVids production chain.

The smoke never deletes data and is a strict plan unless ``--run`` is supplied.
It can create a workflow from one explicitly approved script, or observe one
explicitly selected workflow. Stage 18 standard QA is executed only after the
durable production state reports completion.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

TERMINAL_FAILURES = {"blocked", "failed", "cancelled"}
EXPECTED_PHASES = (
    "audio",
    "narration_alignment",
    "visual_plan",
    "production_timeline",
    "render",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--workflow-id")
    target.add_argument("--roteiro-id")
    parser.add_argument("--orchestrator-url", default="http://localhost:8011")
    parser.add_argument("--timeout-seconds", type=float, default=7200)
    parser.add_argument("--poll-seconds", type=float, default=3)
    parser.add_argument("--report", type=Path, default=Path("production-e2e-smoke.json"))
    parser.add_argument(
        "--resume-blocked",
        action="store_true",
        help="Explicitly request resume when the selected existing workflow is blocked.",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="Required to create/resume production and execute Stage 18 QA.",
    )
    return parser


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def _response_json(response: httpx.Response, operation: str) -> dict[str, Any]:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = response.text[:500]
        raise RuntimeError(
            f"{operation} failed with HTTP {response.status_code}: {detail}"
        ) from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"{operation} returned malformed JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{operation} returned {type(payload).__name__}, expected object")
    return payload


def _get_artifact(client: httpx.Client, workflow_id: str, path: str, label: str) -> dict[str, Any]:
    return _response_json(client.get(f"/workflow/{workflow_id}/{path}"), label)


def _assert_visual_contract(workflow: dict[str, Any], visual_plan: dict[str, Any]) -> None:
    duration = float(workflow.get("duracao_audio_segundos") or 0)
    if duration > 0 and int(visual_plan.get("total_queries") or 0) == 0:
        raise RuntimeError(
            "visual_queries_missing: non-empty final narration cannot pass the "
            "production smoke with VisualPlan completed 0/0"
        )


def run(args: argparse.Namespace) -> int:
    plan = {
        "smoke": "PRODUCTION_E2E_CONTRACT_SMOKE",
        "mode": "run" if args.run else "plan",
        "target": {"workflow_id": args.workflow_id, "roteiro_id": args.roteiro_id},
        "orchestrator_url": args.orchestrator_url.rstrip("/"),
        "expected_chain": [*EXPECTED_PHASES, "qa"],
        "mutations": (
            "create workflow or explicitly resume blocked workflow; execute standard Stage 18 QA"
            if args.run
            else "none"
        ),
        "report": str(args.report),
    }
    if not args.run:
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        print("PLAN ONLY: no HTTP request or production action was executed.")
        return 0

    started = time.monotonic()
    transitions: list[dict[str, Any]] = []
    report: dict[str, Any] = {**plan, "started_at": datetime.now(UTC).isoformat()}
    try:
        with httpx.Client(base_url=plan["orchestrator_url"], timeout=30) as client:
            if args.roteiro_id:
                workflow = _response_json(
                    client.post("/workflow", json={"roteiro_id": args.roteiro_id}),
                    "workflow creation",
                )
            else:
                workflow = _response_json(
                    client.get(f"/workflow/{args.workflow_id}"), "workflow lookup"
                )
            workflow_id = str(workflow.get("id") or args.workflow_id or "")
            if not workflow_id:
                raise RuntimeError("workflow response did not contain an id")
            report["workflow_id"] = workflow_id

            if workflow.get("production_status") == "blocked":
                if not args.resume_blocked:
                    raise RuntimeError(
                        "workflow is blocked; inspect its safe error and rerun with "
                        "--resume-blocked only after correcting the cause"
                    )
                workflow = _response_json(
                    client.post(f"/workflow/{workflow_id}/resume-production"),
                    "workflow resume",
                )

            deadline = time.monotonic() + args.timeout_seconds
            previous: tuple[object, object] | None = None
            while time.monotonic() < deadline:
                workflow = _response_json(
                    client.get(f"/workflow/{workflow_id}"), "workflow polling"
                )
                current = (workflow.get("production_status"), workflow.get("production_phase"))
                if current != previous:
                    transition = {
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                        "production_status": current[0],
                        "production_phase": current[1],
                    }
                    transitions.append(transition)
                    print(json.dumps(transition, ensure_ascii=False))
                    previous = current
                status = workflow.get("production_status")
                if status in TERMINAL_FAILURES:
                    raise RuntimeError(
                        f"production {status}: {workflow.get('production_error_category')} - "
                        f"{workflow.get('production_error_message')}"
                    )
                if status == "completed":
                    break
                time.sleep(args.poll_seconds)
            else:
                raise TimeoutError("production did not complete before timeout")

            artifacts = {
                "narration_alignment": _get_artifact(
                    client, workflow_id, "narration-timeline", "narration alignment"
                ),
                "visual_plan": _get_artifact(client, workflow_id, "visual-plan", "visual plan"),
                "production_timeline": _get_artifact(
                    client, workflow_id, "production-timeline", "production timeline"
                ),
                "render": _get_artifact(client, workflow_id, "render", "render"),
            }
            _assert_visual_contract(workflow, artifacts["visual_plan"])
            qa_result = _response_json(
                client.post(f"/workflow/{workflow_id}/qa-report", params={"mode": "standard"}),
                "Stage 18 QA",
            )
            qa = qa_result.get("report")
            if not isinstance(qa, dict):
                raise RuntimeError("Stage 18 response did not contain a report object")
            qa_passed = qa.get("status") in {"passed", "passed_with_warnings"}

            report.update(
                {
                    "status": "PASS" if qa_passed else "FAIL",
                    "transitions": transitions,
                    "artifacts": artifacts,
                    "stage18": qa,
                    "publication_ready": qa.get("publication_ready"),
                    "wall_seconds": round(time.monotonic() - started, 3),
                    "finished_at": datetime.now(UTC).isoformat(),
                }
            )
            _write_report(args.report, report)
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return 0 if qa_passed else 1
    except Exception as exc:
        report.update(
            {
                "status": "FAIL",
                "failure_category": type(exc).__name__,
                "failure_reason": str(exc),
                "transitions": transitions,
                "wall_seconds": round(time.monotonic() - started, 3),
                "finished_at": datetime.now(UTC).isoformat(),
            }
        )
        _write_report(args.report, report)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 1


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
