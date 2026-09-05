import assert from "node:assert/strict";
import { test } from "node:test";

import { shouldPollAudioEditUpdate } from "./polling";

test("audio edit update polling continues only for non-terminal states", () => {
  assert.equal(shouldPollAudioEditUpdate("pending"), true);
  assert.equal(shouldPollAudioEditUpdate("running"), true);
  assert.equal(shouldPollAudioEditUpdate("completed"), false);
  assert.equal(shouldPollAudioEditUpdate("failed"), false);
});
