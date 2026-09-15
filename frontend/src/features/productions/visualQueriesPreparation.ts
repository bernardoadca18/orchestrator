import {
  ApiError,
  ProductionReadiness,
  RevisionPreparation,
  RevisionReadiness,
  RoteiroDisponivel,
} from "../../api/client";

export interface VisualQueriesPreparationDependencies {
  prepare: (roteiroId: string) => Promise<RevisionPreparation>;
  list: () => Promise<RoteiroDisponivel[]>;
  readiness: (roteiroId: string) => Promise<RevisionReadiness>;
}

export interface PreparedVisualRevisionSelection {
  scripts: RoteiroDisponivel[];
  selectedId: string;
  readiness: RevisionReadiness;
}

export function hasMissingVisualQueries(readiness: ProductionReadiness | null): boolean {
  return readiness?.visual.issues?.some(
    (issue) => issue.code === "visual_queries_missing",
  ) === true;
}

export async function prepareVisualQueriesSelection(
  selectedId: string,
  dependencies: VisualQueriesPreparationDependencies,
): Promise<PreparedVisualRevisionSelection> {
  const result = await dependencies.prepare(selectedId);
  if (
    result.roteiro_id === selectedId
    || result.parent_roteiro_id !== selectedId
    || result.revision_kind !== "visual_enrichment"
  ) {
    throw new Error("O Visual Enrichment retornou uma revisão com linhagem inválida.");
  }

  const scripts = await dependencies.list();
  if (!scripts.some((script) => script.id === result.roteiro_id)) {
    throw new Error("A nova revisão visual não apareceu na lista de revisões.");
  }
  const readiness = await dependencies.readiness(result.roteiro_id);
  return { scripts, selectedId: result.roteiro_id, readiness };
}

export class VisualQueriesPreparationFlight {
  private pending: Promise<PreparedVisualRevisionSelection> | null = null;

  run(
    selectedId: string,
    dependencies: VisualQueriesPreparationDependencies,
  ): Promise<PreparedVisualRevisionSelection> {
    if (this.pending) return this.pending;
    const operation = prepareVisualQueriesSelection(selectedId, dependencies)
      .finally(() => { this.pending = null; });
    this.pending = operation;
    return operation;
  }
}

export function visualQueriesFailure(error: unknown): {
  message: string;
  serviceUnavailable: boolean;
} {
  if (error instanceof ApiError && error.status === 503) {
    return {
      message: "Falha ao gerar VisualQueries. O serviço responsável pelo Visual Enrichment está indisponível.",
      serviceUnavailable: true,
    };
  }
  const detail = error instanceof Error ? error.message : "Falha desconhecida";
  return {
    message: `Falha ao gerar VisualQueries. ${detail}`,
    serviceUnavailable: false,
  };
}
