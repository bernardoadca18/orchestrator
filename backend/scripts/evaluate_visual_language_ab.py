"""Read-only planning evaluation under one Visual Director policy and corpus scope."""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import uuid
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from app.clients.media_indexer_search import (
    MediaIndexerError,
    MediaSearchCandidate,
    MediaSearchResultSet,
    VideoWindowQuality,
)
from app.core.database import task_session
from app.models.workflow import VisualPlan
from app.services.visual_director_service import VisualDirectorService
from app.services.visual_diversity import VisualSourceWindow, evaluate_visual_diversity


class CachedQualityProvider:
    """Reuse immutable window inspections across controlled variants."""

    def __init__(self, base) -> None:
        self.base = base
        self.cache: dict[tuple[uuid.UUID, float, float], VideoWindowQuality] = {}
        self.actual_calls = 0
        self.cache_hits = 0

    def __getattr__(self, name):
        return getattr(self.base, name)

    async def video_window_quality(
        self, media_id: uuid.UUID, *, start_seconds: float, end_seconds: float
    ) -> VideoWindowQuality:
        key = (media_id, start_seconds, end_seconds)
        if key in self.cache:
            self.cache_hits += 1
            return self.cache[key]
        self.actual_calls += 1
        quality = await self.base.video_window_quality(
            media_id, start_seconds=start_seconds, end_seconds=end_seconds
        )
        self.cache[key] = quality
        return quality


class MergedQueryProvider:
    """Evaluation-only union of original and translated retrieval results."""

    def __init__(self, base, transformed_by_raw: dict[str, str]) -> None:
        self.base = base
        self.transformed_by_raw = transformed_by_raw

    def __getattr__(self, name):
        return getattr(self.base, name)

    async def _merged(self, method: str, *args, **kwargs) -> MediaSearchResultSet:
        query = kwargs["query"]
        translated = self.transformed_by_raw.get(query, query)
        first = await getattr(self.base, method)(*args, **kwargs)
        if translated == query:
            return first
        second = await getattr(self.base, method)(*args, **{**kwargs, "query": translated})
        unique: dict[tuple[uuid.UUID, float, float], MediaSearchCandidate] = {}
        for candidate in (*first.results, *second.results):
            key = (candidate.media_id, candidate.start_seconds, candidate.end_seconds)
            if key not in unique or candidate.score > unique[key].score:
                unique[key] = candidate
        ranked = sorted(
            unique.values(),
            key=lambda item: (
                -item.score, str(item.media_id), item.start_seconds, item.end_seconds
            ),
        )[: kwargs["max_results"]]
        return replace(
            first,
            results=tuple(
                replace(candidate, rank=index)
                for index, candidate in enumerate(ranked, 1)
            ),
        )

    async def search_session(self, *args, **kwargs) -> MediaSearchResultSet:
        return await self._merged("search_session", *args, **kwargs)

    async def search_project(self, *args, **kwargs) -> MediaSearchResultSet:
        return await self._merged("search_project", *args, **kwargs)


def summarize(decisions, metrics: dict) -> dict:
    scores = [
        float(item.selected_source["semantic_score"])
        for item in decisions
        if item.selected_source and item.selected_source.get("semantic_score") is not None
    ]
    windows = []
    candidate_sets = {}
    for item in decisions:
        pool = (item.diagnostics or {}).get("candidate_pool", [])
        candidate_sets[str(item.visual_query_id)] = [
            [candidate["media_id"], candidate["start_seconds"], candidate["end_seconds"]]
            for candidate in pool
        ]
        source = item.selected_source
        if not source:
            continue
        windows.append(
            VisualSourceWindow(
                session_id=str(source.get("session_id") or ""),
                media_id=str(source["media_id"]),
                start_seconds=float(source.get("source_start_seconds") or 0),
                end_seconds=float(source.get("source_end_seconds") or 0),
                timeline_duration_seconds=float(
                    item.target_narration_duration_seconds
                    or item.desired_duration_seconds
                    or source.get("source_duration_seconds")
                    or 0
                ),
                explicitly_anchored=item.selection_strategy == "preferred_provenance",
                source_type=str(source.get("source_type") or "gameplay"),
            )
        )
    resolved = sum(item.source_resolution_status == "resolved" for item in decisions)
    diversity = evaluate_visual_diversity(windows)
    return {
        "total_queries": len(decisions),
        "resolved_queries": resolved,
        "unresolved_queries": sum(
            item.source_resolution_status == "unresolved" for item in decisions
        ),
        "failed_queries": sum(item.source_resolution_status == "failed" for item in decisions),
        "resolution_rate": resolved / len(decisions) if decisions else 0,
        "avg_semantic_score": statistics.mean(scores) if scores else None,
        "median_semantic_score": statistics.median(scores) if scores else None,
        "min_semantic_score": min(scores, default=None),
        "quality_rejection_count": metrics.get("quality_rejected_candidates", 0),
        "quality_dependency_error_count": metrics.get("quality_dependency_errors", 0),
        "quality_check_calls": metrics.get("quality_check_calls", 0),
        "candidate_sets": candidate_sets,
        "average_unique_top_k_candidates": statistics.mean(
            len({tuple(value) for value in pool}) for pool in candidate_sets.values()
        )
        if candidate_sets
        else 0,
        "visual_plan_diversity": diversity.to_dict(),
        "decisions": [
            {
                "query_id": str(item.visual_query_id),
                "query": item.query,
                "status": item.source_resolution_status,
                "reason": item.reason,
                "selected_source": item.selected_source,
                "diagnostics": item.diagnostics,
            }
            for item in decisions
        ],
        "service_metrics": metrics,
    }


async def run(args: argparse.Namespace) -> dict:
    async with task_session() as session:
        service = VisualDirectorService(session)
        workflow = await service.workflow_repository.obter_por_id(args.workflow_id, com_blocos=True)
        if workflow is None:
            raise ValueError("workflow not found")
        alignment = await service.alignment_service.current(args.workflow_id)
        if alignment is None:
            raise ValueError("narration alignment not found")
        queries = service._queries(workflow, alignment)
        if args.limit is not None:
            if args.limit < 1:
                raise ValueError("limit must be positive")
            queries = queries[: args.limit]
        scope = service._scope(workflow.roteiro_snapshot, queries)
        if scope is None:
            raise ValueError("media scope unavailable")
        if args.extra_session_ids:
            scope = replace(
                scope,
                session_ids=tuple(dict.fromkeys([*scope.session_ids, *args.extra_session_ids])),
            )
        result: dict[str, Any] = {
            "workflow_id": str(args.workflow_id),
            "strategy_version": service._strategy_version(scope),
            "scope": scope.to_dict(),
            "variants": {},
        }
        base_provider = CachedQualityProvider(service.provider)
        merged_queries = {item.query: item.retrieval_query or item.query for item in queries}
        for name in args.variants:
            print(f"evaluating {name}: {len(queries)} queries", flush=True)
            prior_quality_calls = base_provider.actual_calls
            prior_cache_hits = base_provider.cache_hits
            service.provider = (
                MergedQueryProvider(base_provider, merged_queries)
                if name == "merged"
                else base_provider
            )
            selected_queries = (
                [
                    replace(item, retrieval_query=item.query, retrieval_transform=None)
                    for item in queries
                ]
                if name in {"raw", "merged"}
                else queries
            )
            plan = cast(VisualPlan, SimpleNamespace(id=uuid.uuid4(), diagnostics=None))
            try:
                decisions, _, metrics, _, status = await service._build(
                    plan, selected_queries, scope
                )
            except MediaIndexerError as exc:
                result["variants"][name] = {
                    "technical_failure": exc.category,
                    "diagnostics": plan.diagnostics,
                    "actual_quality_http_calls": base_provider.actual_calls - prior_quality_calls,
                    "shared_quality_cache_hits": base_provider.cache_hits - prior_cache_hits,
                }
                args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
                continue
            result["variants"][name] = {"plan_status": status, **summarize(decisions, metrics)}
            result["variants"][name]["actual_quality_http_calls"] = (
                base_provider.actual_calls - prior_quality_calls
            )
            result["variants"][name]["shared_quality_cache_hits"] = (
                base_provider.cache_hits - prior_cache_hits
            )
            args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
        if {"raw", "transformed"}.issubset(args.variants) and all(
            "candidate_sets" in result["variants"][name] for name in ("raw", "transformed")
        ):
            raw_sets = result["variants"]["raw"]["candidate_sets"]
            transformed_sets = result["variants"]["transformed"]["candidate_sets"]
            result["candidate_set_overlap_by_query"] = {
                key: len(
                    {tuple(value) for value in raw_sets[key]}
                    & {tuple(value) for value in transformed_sets[key]}
                )
                for key in raw_sets
            }
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow-id", type=uuid.UUID, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--variants",
        nargs="+",
        choices=("raw", "transformed", "merged"),
        default=["raw", "transformed", "merged"],
    )
    parser.add_argument("--extra-session-ids", nargs="*", type=uuid.UUID, default=[])
    parser.add_argument("--limit", type=int, default=None, help="Small read-only smoke subset")
    args = parser.parse_args()
    result = asyncio.run(run(args))
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
