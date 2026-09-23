"""Persist VisualPlan and ProductionTimeline, then evaluate pre-render visual QA."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.database import task_session
from app.services.production_orchestration_service import ProductionOrchestrationService
from app.services.production_qa_checks import visual_plan_semantic_metrics
from app.services.production_timeline_service import ProductionTimelineService
from app.services.visual_director_service import VisualDirectorService
from app.services.visual_diversity import timeline_visual_diversity


def json_ready(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {key: json_ready(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [json_ready(item) for item in value]
    return value


async def run(workflow_id: uuid.UUID) -> dict:
    result: dict[str, Any] = {
        "workflow_id": str(workflow_id),
        "captured_at_utc": datetime.now(UTC).isoformat(),
    }
    async with task_session() as session:
        visual = VisualDirectorService(session)
        plan, plan_created = await visual.ensure(workflow_id)
        result["visual_plan"] = {
            "id": str(plan.id),
            "created": plan_created,
            "status": plan.status,
            "strategy_version": plan.strategy_version,
            "total_queries": plan.total_queries,
            "resolved_queries": plan.resolved_queries,
            "unresolved_queries": plan.unresolved_queries,
            "failed_queries": plan.failed_queries,
            "metrics": plan.metrics,
            "diagnostics": plan.diagnostics,
            "semantic_metrics": visual_plan_semantic_metrics(plan),
        }
        if plan.status not in {"completed", "completed_with_gaps"}:
            return result
        timeline_service = ProductionTimelineService(session)
        timeline, timeline_created = await timeline_service.ensure(workflow_id)
        orchestrator = ProductionOrchestrationService(session)
        diversity = timeline_visual_diversity(
            timeline.placements, orchestrator._visual_diversity_policy(timeline)
        )
        semantic = visual_plan_semantic_metrics(plan)
        semantic_failed = bool(
            semantic["entity_violation_count"]
            or (plan.decisions and not semantic["semantic_coverage"])
        )
        qa_status = "BLOCK" if diversity.status == "failed" or semantic_failed else (
            "WARNING" if diversity.status == "warning" else "PASS"
        )
        result["production_timeline"] = {
            "id": str(timeline.id),
            "created": timeline_created,
            "status": timeline.status,
            "visual_plan_id": str(timeline.visual_plan_id),
            "placement_count": len(timeline.placements),
            "gap_count": timeline.gap_count,
            "visual_coverage_ratio": timeline.visual_coverage_ratio,
            "diversity": diversity.to_dict(),
            "pre_render_visual_qa": qa_status,
            "semantic_metrics": semantic,
        }
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow-id", type=uuid.UUID, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(run(args.workflow_id))
    args.output.write_text(
        json.dumps(json_ready(result), ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
