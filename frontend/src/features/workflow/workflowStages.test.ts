import assert from "node:assert/strict";
import test from "node:test";
import {
  productionStages,
  sectionForStage,
  stagePresentation,
} from "./workflowStages";

test("an unknown or legacy phase never claims completed production stages", () => {
  assert.equal(stagePresentation(null, "completed", "audio"), "unknown");
  assert.equal(
    stagePresentation("future_phase", "completed", "render"),
    "unknown",
  );
  assert.equal(
    stagePresentation("render", "completed", "unsupported"),
    "unknown",
  );
});

test("stage projection preserves queue, failure and confirmed earlier phases", () => {
  assert.equal(
    stagePresentation("visual_plan", "queued", "visual_plan"),
    "queued",
  );
  assert.equal(
    stagePresentation("visual_plan", "failed", "visual_plan"),
    "failed",
  );
  assert.equal(
    stagePresentation("visual_plan", "failed", "audio"),
    "completed",
  );
  assert.equal(
    stagePresentation("visual_plan", "failed", "render"),
    "not_started",
  );
  assert.equal(stagePresentation("render", "running", "render"), "active");
  assert.equal(stagePresentation("audio", null, "audio"), "unknown");
});

test("an early completed phase does not imply a later render was completed", () => {
  assert.equal(stagePresentation("audio", "completed", "render"), "unknown");
  assert.equal(stagePresentation("render", "completed", "render"), "completed");
  assert.equal(
    stagePresentation("completed", "completed", "audio"),
    "completed",
  );
  assert.equal(
    stagePresentation("completed", "completed", "render"),
    "completed",
  );
  assert.equal(stagePresentation("completed", "running", "render"), "unknown");
});

test("manual and storyboard paths use their supported stages and real sections", () => {
  assert.deepEqual(
    productionStages("manual_edit_package").map(([key]) => key),
    ["audio", "manual_edit_package"],
  );
  assert.equal(
    stagePresentation("manual_edit_package", "running", "audio", false, true),
    "completed",
  );
  assert.equal(
    stagePresentation("storyboard_assets", "running", "storyboard", true),
    "completed",
  );
  assert.equal(sectionForStage("storyboard"), "overview");
  assert.equal(sectionForStage("manual_edit_package"), "overview");
  assert.equal(sectionForStage("narration_alignment"), "narration");
  assert.equal(sectionForStage("qa"), "render");
});
