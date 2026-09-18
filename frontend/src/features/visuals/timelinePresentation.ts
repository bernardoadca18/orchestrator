import type { ProductionTimelineDetail } from "../../api/operator";

type Coverage = Pick<ProductionTimelineDetail, "status" | "duration_seconds" | "gap_count" | "visual_gap_seconds" | "visual_coverage_ratio" | "unplaced_query_count">;

export function presentTimeline(timeline: Coverage) {
  if (!["completed", "completed_with_gaps"].includes(timeline.status)) {
    return { title: "Timeline indisponível", detail: "" };
  }
  const complete = timeline.gap_count === 0 && Math.abs(timeline.visual_gap_seconds) <= 0.000001
    && Math.abs(1 - timeline.visual_coverage_ratio) * timeline.duration_seconds <= 0.000001;
  return {
    title: complete ? "Timeline completa" : "Timeline incompleta",
    detail: complete
      ? (timeline.unplaced_query_count > 0 ? `${timeline.unplaced_query_count} ${timeline.unplaced_query_count === 1 ? "intenção visual não satisfeita" : "intenções visuais não satisfeitas"} · não bloqueia o render` : "")
      : `${timeline.gap_count} ${timeline.gap_count === 1 ? "gap visual" : "gaps visuais"} · ${timeline.visual_gap_seconds.toFixed(1)}s`,
  };
}
