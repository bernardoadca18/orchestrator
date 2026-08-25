"""Opt-in Stage 18 smoke against an existing completed Stage 17 workflow."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.core.database import task_session
from app.models.workflow import WorkflowExecucao
from app.services.production_qa_service import ProductionQAService


async def run(workflow_id: uuid.UUID | None, mode: str, force: bool) -> int:
    async with task_session() as session:
        if workflow_id is None:
            statement = (
                select(WorkflowExecucao.id)
                .where(WorkflowExecucao.production_status == "completed")
                .order_by(WorkflowExecucao.production_completed_at.desc())
                .limit(1)
            )
            workflow_id = (await session.execute(statement)).scalar_one_or_none()
        if workflow_id is None:
            print(f"STAGE18_{mode.upper()}_QA_SMOKE: NOT RUN (no completed workflow)")
            return 2
        result = await ProductionQAService(session).ensure(
            workflow_id,
            mode="deep" if mode == "deep" else "standard",
            force=force,
        )
        report = result.report
        print(
            json.dumps(
                {
                    "workflow_id": str(workflow_id),
                    "qa_report_id": str(report.id),
                    "mode": report.qa_mode,
                    "status": report.status,
                    "checks": report.check_count,
                    "passed": report.passed_count,
                    "warnings": report.warning_count,
                    "failed": report.failed_count,
                    "blocked": report.blocked_count,
                    "publication_ready": report.publication_ready,
                    "runtime_ms": (report.metrics or {}).get("total_qa_time_ms"),
                },
                indent=2,
            )
        )
        label = f"STAGE18_{mode.upper()}_QA_SMOKE"
        print(f"{label}: {'PASS' if report.publication_ready else 'FAIL'}")
        return 0 if report.publication_ready else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow-id", type=uuid.UUID)
    parser.add_argument("--mode", choices=("standard", "deep"), default="standard")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    return asyncio.run(run(args.workflow_id, args.mode, args.force))


if __name__ == "__main__":
    sys.exit(main())
