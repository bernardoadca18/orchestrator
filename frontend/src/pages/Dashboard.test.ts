import assert from "node:assert/strict";
import test from "node:test";
import { isActiveProductionStatus } from "./dashboardState";

test("only server-owned queued and running production states are active", () => {
  assert.equal(isActiveProductionStatus("queued"), true);
  assert.equal(isActiveProductionStatus("running"), true);
  assert.equal(isActiveProductionStatus("completed"), false);
  assert.equal(isActiveProductionStatus("failed"), false);
});
