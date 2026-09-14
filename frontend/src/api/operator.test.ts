import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { ApiError } from "./client";
import { operatorApi } from "./operator";

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });

test("human review sends expected render identity and idempotency key", async () => {
  let requestBody = "";
  let requestInit: RequestInit | undefined;
  globalThis.fetch = async (_input, init) => {
    requestInit = init; requestBody = String(init?.body);
    return new Response(JSON.stringify({ created: true, review: { status: "approved" } }), { status: 201, headers: { "Content-Type": "application/json" } });
  };
  await operatorApi.submitReview("wf", { workflow_render_id: "render", render_fingerprint: "a".repeat(64), output_sha256: "b".repeat(64), status: "approved" }, "review:key");
  assert.equal(requestInit?.method, "POST");
  assert.equal(new Headers(requestInit?.headers).get("Idempotency-Key"), "review:key");
  assert.equal(JSON.parse(requestBody).workflow_render_id, "render");
});

test("music override and automatic restore use revision-safe contracts", async () => {
  const requests: Array<{ url: string; init?: RequestInit }> = [];
  globalThis.fetch = async (input, init) => {
    requests.push({ url: String(input), init });
    return new Response(JSON.stringify({ workflow_id: "wf", asset_id: null, revision: 4, changed: true, production_status: "queued", production_phase: "render" }), { status: 200, headers: { "Content-Type": "application/json" } });
  };
  await operatorApi.setMusicOverride("wf", "track", 2, "set:key");
  await operatorApi.clearMusicOverride("wf", 3, "clear:key");
  assert.equal(requests[0].init?.method, "PUT");
  assert.deepEqual(JSON.parse(String(requests[0].init?.body)), { asset_id: "track", expected_revision: 2 });
  assert.equal(requests[1].init?.method, "DELETE");
  assert.deepEqual(JSON.parse(String(requests[1].init?.body)), { expected_revision: 3 });
});

test("structured stale conflict remains distinguishable", async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: { category: "stale_render", message: "Render mudou" } }), { status: 409, headers: { "Content-Type": "application/json" } });
  await assert.rejects(operatorApi.clearMusicOverride("wf", 1, "key"), (error: unknown) => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.status, 409);
    assert.equal(error.category, "stale_render");
    return true;
  });
});

test("production filters are sent to the server-owned projection", async () => {
  let requestedUrl = "";
  globalThis.fetch = async (input) => {
    requestedUrl = String(input);
    return new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } });
  };
  await operatorApi.listWorkflows({ state: "needs_review", search: "gta", ordering: "title" });
  const url = new URL(requestedUrl);
  assert.equal(url.searchParams.get("state"), "needs_review");
  assert.equal(url.searchParams.get("search"), "gta");
  assert.equal(url.searchParams.get("ordering"), "title");
});
