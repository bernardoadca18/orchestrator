import assert from "node:assert/strict";
import test from "node:test";
import { legacyWorkflowTarget, workflowPath, workflowSections } from "./routes";

test("legacy workflow route redirects to the overview deep link", () => {
  assert.equal(legacyWorkflowTarget("abc"), "/productions/abc/overview");
});

test("every workflow workspace section has a refresh-safe URL", () => {
  assert.deepEqual(workflowSections.map(([section]) => workflowPath("abc", section)), [
    "/productions/abc/overview",
    "/productions/abc/narration",
    "/productions/abc/visuals",
    "/productions/abc/presentation",
    "/productions/abc/program-audio",
    "/productions/abc/render",
    "/productions/abc/history",
  ]);
});
