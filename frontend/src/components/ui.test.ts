import assert from "node:assert/strict";
import test from "node:test";
import { actionAvailability } from "./ui";

test("disabled capability preserves the server reason", () => {
  assert.deepEqual(actionAvailability({ allowed: false, reason: "Estado stale", invalidates: [] }), { disabled: true, reason: "Estado stale" });
  assert.deepEqual(actionAvailability({ allowed: true, reason: null, invalidates: [] }), { disabled: false, reason: null });
});
