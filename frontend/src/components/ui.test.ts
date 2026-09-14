import assert from "node:assert/strict";
import test from "node:test";
import { actionAvailability, statusTone } from "./ui";

test("disabled capability preserves the server reason", () => {
  assert.deepEqual(actionAvailability({ allowed: false, reason: "Estado stale", invalidates: [] }), { disabled: true, reason: "Estado stale" });
  assert.deepEqual(actionAvailability({ allowed: true, reason: null, invalidates: [] }), { disabled: false, reason: null });
});

test("status tones preserve operator health and attention semantics", () => {
  assert.equal(statusTone("healthy"), "success");
  assert.equal(statusTone("monetization allowed"), "success");
  assert.equal(statusTone("degraded"), "warning");
  assert.equal(statusTone("needs_attention"), "warning");
  assert.equal(statusTone("unavailable"), "danger");
  assert.equal(statusTone("failed"), "danger");
});
