import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { ApiError, api, isExpectedNotFound } from "./client";

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

test("callers can recognize an expected 404", async () => {
  globalThis.fetch = async () => new Response(
    JSON.stringify({ detail: "render not created" }),
    { status: 404, headers: { "Content-Type": "application/json" } },
  );

  await assert.rejects(api.obterRender("workflow"), (error: unknown) => {
    assert.ok(error instanceof ApiError);
    assert.equal(isExpectedNotFound(error), true);
    assert.equal(error.status, 404);
    return true;
  });
});

test("a server error is not treated as a missing artifact", async () => {
  globalThis.fetch = async () => new Response(
    JSON.stringify({ detail: "composer unavailable" }),
    { status: 503, headers: { "Content-Type": "application/json" } },
  );

  await assert.rejects(api.obterQAReport("workflow"), (error: unknown) => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.kind, "http");
    assert.equal(error.status, 503);
    assert.equal(isExpectedNotFound(error), false);
    return true;
  });
});

test("network failures retain their category", async () => {
  globalThis.fetch = async () => { throw new TypeError("offline"); };

  await assert.rejects(api.obterWorkflow("workflow"), (error: unknown) => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.kind, "network");
    assert.equal(error.status, undefined);
    return true;
  });
});

test("malformed successful responses retain their category", async () => {
  globalThis.fetch = async () => new Response("not-json", { status: 200 });

  await assert.rejects(api.obterWorkflow("workflow"), (error: unknown) => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.kind, "malformed_response");
    assert.equal(error.status, 200);
    return true;
  });
});
