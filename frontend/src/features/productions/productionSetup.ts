import { ProductionReadiness, RoteiroDisponivel } from "../../api/client";

export function defaultRevisionId(scripts: RoteiroDisponivel[]): string {
  return scripts.find((script) => script.is_latest)?.id ?? scripts[0]?.id ?? "";
}

export function isHistoricalRevision(script: RoteiroDisponivel): boolean {
  return !script.is_latest;
}

export function canStartProduction(
  script: RoteiroDisponivel,
  readiness: ProductionReadiness | null,
): boolean {
  return script.status === "aprovado" && readiness?.status === "ready";
}

export function hasReadinessIssue(
  readiness: ProductionReadiness | null,
  code: string,
): boolean {
  return readiness?.issues.some((issue) => issue.code === code) === true;
}
