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

test("explicit production resume uses the canonical operator endpoint", async () => {
  let requestedUrl = "";
  let requestedInit: RequestInit | undefined;
  globalThis.fetch = async (input, init) => {
    requestedUrl = String(input);
    requestedInit = init;
    return new Response(JSON.stringify({ production_status: "queued", production_phase: "render" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };

  const result = await api.retomarProducao("workflow-1");

  assert.match(requestedUrl, /\/workflow\/workflow-1\/resume-production$/);
  assert.equal(requestedInit?.method, "POST");
  assert.equal(result.production_status, "queued");
  assert.equal(result.production_phase, "render");
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

test("chunk regeneration uses the real idempotent contract without a seed body", async () => {
  let requestedUrl = "";
  let requestedInit: RequestInit | undefined;
  globalThis.fetch = async (input, init) => {
    requestedUrl = String(input);
    requestedInit = init;
    return new Response(JSON.stringify({
      take_id: "take-2",
      chunk_id: "chunk-1",
      chunk_index: 3,
      take_number: 2,
      seed: 991,
      status: "candidate",
      audio_url: "/candidate.wav",
      duration_seconds: 10.72,
    }), { status: 200, headers: { "Content-Type": "application/json" } });
  };

  const result = await api.regenerarChunk("workflow-1", "block-1", 3, "chunk-regenerate:key");

  assert.match(requestedUrl, /\/workflow\/workflow-1\/blocos\/block-1\/chunks\/3\/regenerate$/);
  assert.equal(requestedInit?.method, "POST");
  assert.equal(new Headers(requestedInit?.headers).get("Idempotency-Key"), "chunk-regenerate:key");
  assert.equal(requestedInit?.body, undefined);
  assert.equal(result.take_id, "take-2");
  assert.equal(result.status, "candidate");
});

test("chunk promotion parses the deferred-publication response", async () => {
  let requestedUrl = "";
  let requestedInit: RequestInit | undefined;
  globalThis.fetch = async (input, init) => {
    requestedUrl = String(input);
    requestedInit = init;
    return new Response(JSON.stringify({
      take_id: "take-2",
      status: "promoted",
      chunk_status: "active",
      audio_edits_pending: true,
      audio_edit_revision: 4,
      final_audio_sha256: "old-sha",
      message: "Take selecionado.",
    }), { status: 200, headers: { "Content-Type": "application/json" } });
  };

  const result = await api.promoverChunkTake("workflow-1", "take-2");

  assert.match(requestedUrl, /\/workflow\/workflow-1\/chunk-takes\/take-2\/promote$/);
  assert.equal(requestedInit?.method, "POST");
  assert.equal(result.audio_edits_pending, true);
  assert.equal(result.final_audio_sha256, "old-sha");
});

test("chunk discard calls the historical rejection contract", async () => {
  let requestedUrl = "";
  let requestedInit: RequestInit | undefined;
  globalThis.fetch = async (input, init) => {
    requestedUrl = String(input);
    requestedInit = init;
    return new Response(JSON.stringify({ take_id: "take-2", status: "rejected" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };

  const result = await api.descartarChunkTake("workflow-1", "take-2");

  assert.match(requestedUrl, /\/workflow\/workflow-1\/chunk-takes\/take-2\/discard$/);
  assert.equal(requestedInit?.method, "POST");
  assert.deepEqual(result, { take_id: "take-2", status: "rejected" });
});

test("audio edit apply sends its key and supports durable and no-pending responses", async () => {
  const requests: Array<{ url: string; init?: RequestInit }> = [];
  let call = 0;
  globalThis.fetch = async (input, init) => {
    requests.push({ url: String(input), init });
    call += 1;
    const body = call === 1
      ? {
          workflow_id: "workflow-1",
          update_id: "update-1",
          status: "pending",
          created: true,
          audio_edit_revision: 3,
          audio_materialized_revision: 1,
          audio_edits_pending: true,
          final_audio_sha256: "old-sha",
        }
      : {
          workflow_id: "workflow-1",
          update_id: null,
          status: "no_pending",
          created: false,
          audio_edit_revision: 3,
          audio_materialized_revision: 3,
          audio_edits_pending: false,
          final_audio_sha256: "new-sha",
        };
    return new Response(JSON.stringify(body), {
      status: 202,
      headers: { "Content-Type": "application/json" },
    });
  };

  const pending = await api.aplicarEdicoesAudio("workflow-1", "audio-edit-apply:key");
  const noPending = await api.aplicarEdicoesAudio("workflow-1", "audio-edit-apply:new-key");

  assert.match(requests[0].url, /\/workflow\/workflow-1\/audio-edits\/apply$/);
  assert.equal(requests[0].init?.method, "POST");
  assert.equal(new Headers(requests[0].init?.headers).get("Idempotency-Key"), "audio-edit-apply:key");
  assert.equal(pending.update_id, "update-1");
  assert.equal(noPending.status, "no_pending");
  assert.equal(noPending.update_id, null);
});

test("audio edit update status uses the operation-specific endpoint", async () => {
  let requestedUrl = "";
  let requestedInit: RequestInit | undefined;
  globalThis.fetch = async (input, init) => {
    requestedUrl = String(input);
    requestedInit = init;
    return new Response(JSON.stringify({
      id: "update-1",
      workflow_execucao_id: "workflow-1",
      idempotency_key: "apply-key",
      target_revision: 3,
      status: "failed",
      published_audio_sha256: null,
      error_stage: "audio_materialization",
      error_message: "missing chunk",
      created_at: "2026-09-05T10:00:00Z",
      started_at: "2026-09-05T10:00:01Z",
      materialized_at: null,
      completed_at: "2026-09-05T10:00:02Z",
      updated_at: "2026-09-05T10:00:02Z",
    }), { status: 200, headers: { "Content-Type": "application/json" } });
  };

  const result = await api.obterAudioEditUpdate("workflow-1", "update-1");

  assert.match(requestedUrl, /\/workflow\/workflow-1\/audio-edits\/updates\/update-1$/);
  assert.equal(requestedInit?.method, undefined);
  assert.equal(result.status, "failed");
  assert.equal(result.error_stage, "audio_materialization");
});
