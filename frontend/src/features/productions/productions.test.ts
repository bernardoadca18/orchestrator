import assert from "node:assert/strict";
import test from "node:test";
import { productionViewState } from "./ProductionsPage";

test("productions models loading, error and empty states explicitly", () => {
  assert.equal(productionViewState(0, true, null), "loading");
  assert.equal(productionViewState(0, false, "offline"), "error");
  assert.equal(productionViewState(0, false, null), "empty");
  assert.equal(productionViewState(1, false, null), "ready");
});
