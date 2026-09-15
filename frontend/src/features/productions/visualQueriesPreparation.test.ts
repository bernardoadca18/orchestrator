import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  ApiError,
  ProductionReadiness,
  RevisionPreparation,
  RevisionReadiness,
  RoteiroDisponivel,
} from "../../api/client";
import { VisualQueriesActionPanel } from "./VisualQueriesActionPanel";
import {
  hasMissingVisualQueries,
  prepareVisualQueriesSelection,
  VisualQueriesPreparationDependencies,
  VisualQueriesPreparationFlight,
  visualQueriesFailure,
} from "./visualQueriesPreparation";

function report(visualCode: string): ProductionReadiness {
  return {
    status: "not_ready",
    profile: "game_analysis",
    editorial_status: "aprovado",
    visual: { status: "not_ready", issues: [{ block_order: 0, code: visualCode }] },
    media_scope: { required: true, status: "ready" },
    issues: [{ code: "visual_readiness_not_ready" }],
  };
}

function script(id: string, version: number, kind: string): RoteiroDisponivel {
  return {
    id,
    ideia_id: "idea",
    versao: version,
    titulo: "Roteiro",
    duracao_total_segundos: 60,
    num_blocos: 1,
    parent_roteiro_id: version === 6 ? "v5" : null,
    revision_kind: kind,
    status: "aprovado",
    created_at: "2026-09-15T00:00:00Z",
    is_latest: version === 6,
    media_bound: true,
  };
}

function preparation(): RevisionPreparation {
  return {
    roteiro_id: "v6",
    parent_roteiro_id: "v5",
    versao: 6,
    revision_kind: "visual_enrichment",
    status: "aprovado",
    production_readiness: report("none"),
  };
}

function ready(): RevisionReadiness {
  return {
    production_readiness: { ...report("none"), status: "ready", visual: { status: "ready", issues: [] }, issues: [] },
    blockers: [],
  };
}

test("missing VisualQueries renders its dedicated action", () => {
  assert.equal(hasMissingVisualQueries(report("visual_queries_missing")), true);
  const html = renderToStaticMarkup(createElement(VisualQueriesActionPanel, {
    preparing: false,
    serviceUnavailable: false,
    onGenerate: () => undefined,
  }));
  assert.match(html, /Gerar VisualQueries/);
  assert.match(html, /A narração não será modificada/);
});

test("an unrelated visual failure does not become a missing-query action", () => {
  assert.equal(hasMissingVisualQueries(report("visual_query_overlap")), false);
  assert.equal(hasMissingVisualQueries(report("invalid_provenance")), false);
});

test("loading state disables the VisualQueries button", () => {
  const html = renderToStaticMarkup(createElement(VisualQueriesActionPanel, {
    preparing: true,
    serviceUnavailable: false,
    onGenerate: () => undefined,
  }));
  assert.match(html, /<button disabled="">Gerando VisualQueries…<\/button>/);
});

test("Ideas Hub outage is explicit and removes the actionable button", () => {
  const failure = visualQueriesFailure(new ApiError("upstream offline", "http", 503));
  const html = renderToStaticMarkup(createElement(VisualQueriesActionPanel, {
    preparing: false,
    serviceUnavailable: failure.serviceUnavailable,
    onGenerate: () => undefined,
  }));

  assert.match(failure.message, /Visual Enrichment está indisponível/);
  assert.match(html, /Visual Enrichment está indisponível/);
  assert.doesNotMatch(html, /<button/);
});

test("preparation targets v5, selects returned v6, and refetches readiness for v6", async () => {
  const calls: string[] = [];
  const dependencies: VisualQueriesPreparationDependencies = {
    prepare: async (id) => { calls.push(`prepare:${id}`); return preparation(); },
    list: async () => { calls.push("list"); return [script("v6", 6, "visual_enrichment"), script("v5", 5, "media_binding")]; },
    readiness: async (id) => { calls.push(`readiness:${id}`); return ready(); },
  };

  const result = await prepareVisualQueriesSelection("v5", dependencies);

  assert.equal(result.selectedId, "v6");
  assert.equal(result.readiness.production_readiness.status, "ready");
  assert.deepEqual(calls, ["prepare:v5", "list", "readiness:v6"]);
});

test("failure leaves the old revision selected", async () => {
  let selectedId = "v5";
  const dependencies: VisualQueriesPreparationDependencies = {
    prepare: async () => { throw new Error("provider failure"); },
    list: async () => [],
    readiness: async () => ready(),
  };

  try {
    selectedId = (await prepareVisualQueriesSelection(selectedId, dependencies)).selectedId;
  } catch (error) {
    assert.match(String(error), /provider failure/);
  }

  assert.equal(selectedId, "v5");
});

test("double click shares one in-flight Visual Enrichment request", async () => {
  let release!: (value: RevisionPreparation) => void;
  let prepareCalls = 0;
  const dependencies: VisualQueriesPreparationDependencies = {
    prepare: async () => {
      prepareCalls += 1;
      return await new Promise<RevisionPreparation>((resolve) => { release = resolve; });
    },
    list: async () => [script("v6", 6, "visual_enrichment")],
    readiness: async () => ready(),
  };
  const flight = new VisualQueriesPreparationFlight();

  const first = flight.run("v5", dependencies);
  const second = flight.run("v5", dependencies);
  assert.equal(first, second);
  assert.equal(prepareCalls, 1);
  release(preparation());
  const [firstResult, secondResult] = await Promise.all([first, second]);

  assert.equal(firstResult.selectedId, "v6");
  assert.equal(secondResult.selectedId, "v6");
  assert.equal(prepareCalls, 1);
});
