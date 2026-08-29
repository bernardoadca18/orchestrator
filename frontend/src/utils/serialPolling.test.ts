import assert from "node:assert/strict";
import { test } from "node:test";

import { pollingDelayMs, startSerialPolling } from "./serialPolling";

const wait = (milliseconds: number) =>
  new Promise<void>((resolve) => { setTimeout(resolve, milliseconds); });

test("serial polling never overlaps requests", async () => {
  let release: (() => void) | undefined;
  let calls = 0;
  const task = () => {
    calls += 1;
    return new Promise<void>((resolve) => { release = resolve; });
  };

  const stop = startSerialPolling(task, { intervalMs: 5 });
  await wait(20);
  assert.equal(calls, 1);
  release?.();
  await wait(10);
  assert.equal(calls, 2);
  stop();
});

test("serial polling aborts the active request when stopped", async () => {
  let observedSignal: AbortSignal | undefined;
  const task = (signal: AbortSignal) => {
    observedSignal = signal;
    return new Promise<void>(() => undefined);
  };

  const stop = startSerialPolling(task, { intervalMs: 1_000 });
  await wait(0);
  assert.equal(observedSignal?.aborted, false);
  stop();
  assert.equal(observedSignal?.aborted, true);
});

test("polling backoff is exponential and bounded", () => {
  assert.equal(pollingDelayMs(100, 250, 0), 100);
  assert.equal(pollingDelayMs(100, 250, 1), 200);
  assert.equal(pollingDelayMs(100, 250, 2), 250);
  assert.equal(pollingDelayMs(100, 250, 8), 250);
});
