import assert from "node:assert/strict";
import test from "node:test";
import { ProductionReadiness, RoteiroDisponivel } from "../../api/client";
import {
  canStartProduction,
  defaultRevisionId,
  hasReadinessIssue,
  isHistoricalRevision,
} from "./productionSetup";

function script(overrides: Partial<RoteiroDisponivel> = {}): RoteiroDisponivel {
  return {
    id: "v2",
    ideia_id: "idea",
    versao: 2,
    titulo: "Roteiro",
    duracao_total_segundos: 60,
    num_blocos: 1,
    parent_roteiro_id: "v1",
    revision_kind: "human_edit",
    status: "aprovado",
    created_at: "2026-01-01T00:00:00Z",
    is_latest: false,
    media_bound: true,
    ...overrides,
  };
}

function readiness(status: "ready" | "not_ready", codes: string[] = []): ProductionReadiness {
  return {
    status,
    profile: "game_analysis",
    editorial_status: "aprovado",
    visual: { status: "ready", issues: [] },
    media_scope: { required: true, status: status === "ready" ? "ready" : "missing" },
    issues: codes.map((code) => ({ code })),
  };
}

test("latest revision is only the default and historical revisions remain selectable", () => {
  const historical = script();
  const latest = script({ id: "v4", versao: 4, is_latest: true });
  assert.equal(defaultRevisionId([latest, historical]), "v4");
  assert.equal(isHistoricalRevision(historical), true);
  assert.equal(isHistoricalRevision(latest), false);
});

test("an approved historical revision can start when its exact readiness is ready", () => {
  assert.equal(canStartProduction(script(), readiness("ready")), true);
});

test("media and visual gates remain strict and actionable", () => {
  const report = readiness("not_ready", ["media_scope_missing"]);
  assert.equal(hasReadinessIssue(report, "media_scope_missing"), true);
  assert.equal(canStartProduction(script(), report), false);
  assert.equal(canStartProduction(script({ status: "rascunho" }), readiness("ready")), false);
});
