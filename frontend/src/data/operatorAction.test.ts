import assert from "node:assert/strict";
import test from "node:test";
import { SingleFlightAction } from "./operatorAction";

test("operator action rejects a double submit", async () => {
  const action = new SingleFlightAction();
  let release!: () => void;
  const pending = action.run(() => new Promise<void>((resolve) => { release = resolve; }));
  await assert.rejects(() => action.run(async () => undefined), /já está em andamento/);
  release();
  await pending;
  assert.equal(action.submitting, false);
});
