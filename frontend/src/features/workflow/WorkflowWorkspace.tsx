import { sectionForStage } from "./workflowStages";
import { NavLink, Outlet, useOutletContext, useParams } from "react-router-dom";
import { OperatorWorkflowSnapshot } from "../../api/operator";
import { workflowPath, workflowSections } from "../../app/routes";
import {
  Alert,
  ErrorState,
  LoadingState,
  StatusBadge,
  statusLabel,
} from "../../components/ui";
import { useOperatorSnapshot } from "../../hooks/useOperatorSnapshot";

export interface WorkflowOutletContext {
  snapshot: OperatorWorkflowSnapshot;
  refresh: () => void;
}

export function useWorkflowContext() {
  return useOutletContext<WorkflowOutletContext>();
}

export default function WorkflowWorkspace() {
  const { workflowId = "" } = useParams();
  const { data, error, refresh } = useOperatorSnapshot(workflowId);
  const hasCurrentWorkflow = data?.workflow.id === workflowId;
  if (!hasCurrentWorkflow && error)
    return (
      <div className="page">
        <ErrorState message={error} />
      </div>
    );
  if (!data || !hasCurrentWorkflow)
    return (
      <div className="page">
        <LoadingState label="Carregando workspace…" />
      </div>
    );
  const workflow = data.workflow;
  const phaseSection = sectionForStage(workflow.production_phase || "");
  return (
    <div className="workflow-workspace">
      <header className="workflow-header">
        <div>
          <p className="eyebrow">
            Produção <code>{workflow.id.slice(0, 8)}</code>
          </p>
          <h1>{workflow.title}</h1>
          <div className="status-row">
            <StatusBadge
              value={workflow.production_status || workflow.legacy_status}
            />
            <span>
              Fase:{" "}
              <strong>
                {statusLabel(workflow.production_phase || "áudio legado")}
              </strong>
            </span>
            <span>
              Perfil: <strong>{workflow.production_profile || "—"}</strong>
            </span>
            <span>
              Duração:{" "}
              <strong>
                {workflow.audio_duration_seconds?.toFixed(1) ||
                  workflow.script_duration_seconds?.toFixed(0) ||
                  "—"}
                s
              </strong>
            </span>
            <span>
              QA: <StatusBadge value={workflow.qa?.status || "não executado"} />
            </span>
            <span>
              Revisão: <StatusBadge value={workflow.human_review.state} />
            </span>
          </div>
        </div>
        <NavLink className="secondary-link" to="/productions">
          Voltar à fila
        </NavLink>
      </header>
      {error && (
        <Alert tone="warning">
          Falha na última atualização: {error}. Exibindo o último snapshot
          válido.
        </Alert>
      )}
      {workflow.attention.length > 0 && (
        <Alert
          tone={
            workflow.attention.some((item) => item.severity === "critical")
              ? "danger"
              : "warning"
          }
        >
          <strong>Requer atenção:</strong>{" "}
          {workflow.attention.map((item) => item.message).join(" ")}
        </Alert>
      )}
      <section className="next-step" aria-label="Orientação da produção">
        <div>
          <span className="eyebrow">Próximo passo</span>
          <strong>
            {workflow.current_error_message
              ? "Resolva o bloqueio para continuar"
              : ["pending", "stale"].includes(workflow.human_review.state)
                ? "Revise o vídeo e registre sua decisão"
                : workflow.capabilities.can_resume?.allowed
                  ? "Produção pronta para retomar"
                  : workflow.capabilities.can_retry?.allowed
                    ? "Confira a falha e tente a etapa novamente"
                    : workflow.production_status === "completed"
                      ? "Confira o resultado e a aprovação humana"
                      : "Acompanhe a etapa atual"}
          </strong>
          <p>
            {workflow.current_error_message ||
              workflow.attention[0]?.message ||
              "As etapas e ações refletem a última confirmação do Orchestrator."}
          </p>
        </div>
        <NavLink
          className="secondary-link"
          to={workflowPath(
            workflowId,
            ["pending", "stale"].includes(workflow.human_review.state) ||
              workflow.production_status === "completed"
              ? "render"
              : workflow.current_error_message ||
                  workflow.capabilities.can_retry?.allowed ||
                  workflow.capabilities.can_resume?.allowed
                ? "overview"
                : phaseSection,
          )}
        >
          Ver detalhes →
        </NavLink>
      </section>
      <nav className="workflow-nav" aria-label="Áreas da produção">
        {workflowSections.map(([section, label]) => (
          <NavLink key={section} to={workflowPath(workflowId, section)}>
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="workspace-content">
        <Outlet context={{ snapshot: data, refresh }} />
      </div>
    </div>
  );
}
