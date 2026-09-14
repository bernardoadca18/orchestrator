import assert from "node:assert/strict";
import test from "node:test";
import { ColdArtifactCache } from "./coldArtifactCache";

test("cold artifact is not refetched while its fingerprint is stable", async () => {
  const cache = new ColdArtifactCache();
  let calls = 0;
  const loader = async () => ({ call: ++calls });
  const first = await cache.load("wf:visual-plan", "fingerprint-a", loader);
  const second = await cache.load("wf:visual-plan", "fingerprint-a", loader);
  assert.equal(calls, 1);
  assert.strictEqual(first, second);
});

test("new identity invalidates cached artifact and concurrent reads share one request", async () => {
  const cache = new ColdArtifactCache();
  let calls = 0;
  const loader = async () => ({ call: ++calls });
  await cache.load("wf:timeline", "a", loader);
  const [first, second] = await Promise.all([
    cache.load("wf:timeline", "b", loader),
    cache.load("wf:timeline", "b", loader),
  ]);
  assert.equal(calls, 2);
  assert.strictEqual(first, second);
});

test("successful action invalidation refreshes only named cold resources", async () => {
  const cache = new ColdArtifactCache();
  let renderCalls = 0;
  let visualCalls = 0;
  await cache.load("wf:render", "r1", async () => ++renderCalls);
  await cache.load("wf:visual-plan", "v1", async () => ++visualCalls);
  cache.invalidateWorkflow("wf", ["render"]);
  await cache.load("wf:render", "r1", async () => ++renderCalls);
  await cache.load("wf:visual-plan", "v1", async () => ++visualCalls);
  assert.equal(renderCalls, 2);
  assert.equal(visualCalls, 1);
});
