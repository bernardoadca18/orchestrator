import assert from "node:assert/strict";
import test from "node:test";
import { presentTimeline } from "./timelinePresentation";

const covered = { status: "completed_with_gaps", duration_seconds: 30, gap_count: 0, visual_gap_seconds: 0, visual_coverage_ratio: 1, unplaced_query_count: 1 };

test("legacy covered timeline displays query intent as a nonblocking warning", () => {
  assert.equal(presentTimeline(covered).title, "Timeline completa");
  assert.match(presentTimeline(covered).detail, /1 intenção visual não satisfeita · não bloqueia/);
});

test("actual temporal gap displays missing duration", () => {
  assert.deepEqual(presentTimeline({ ...covered, gap_count: 1, visual_gap_seconds: 2, visual_coverage_ratio: 28 / 30 }), {
    title: "Timeline incompleta", detail: "1 gap visual · 2.0s",
  });
});
