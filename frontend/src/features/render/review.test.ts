import assert from "node:assert/strict";
import test from "node:test";
import { reviewBanner } from "./RenderPage";

test("current approval and changes requested are unambiguous", () => {
  assert.equal(reviewBanner("approved"), "APPROVED");
  assert.equal(reviewBanner("changes_requested"), "CHANGES REQUESTED");
});

test("new render never inherits historical approval", () => {
  assert.equal(reviewBanner("stale", "approved"), "APPROVED HISTÓRICA — RENDER CORRENTE NÃO REVISADO");
  assert.equal(reviewBanner("pending"), "RENDER CORRENTE NÃO REVISADO");
});
