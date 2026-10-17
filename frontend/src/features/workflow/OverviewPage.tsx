import { Link } from "react-router-dom";
import { workflowPath } from "../../app/routes";
import {
  productionStages,
  sectionForStage,
  stagePresentation,
} from "./workflowStages";
import { StoryboardPanel } from "./StoryboardPanel";
import { ManualPackagePanel } from "./ManualPackagePanel";
import { useState } from "react";
import { api } from "../../api/client";
import {
  Alert,
  ActionButton,
  Card,
  ConfirmDialog,
  DefinitionList,
  Diagnostics,
  Progress,
  StatusBadge,
  statusLabel,
} from "../../components/ui";
import { coldArtifactCache } from "../../data/coldArtifactCache";
import { createActionKey } from "../../data/operatorAction";
import { useOperatorAction } from "../../hooks/useOperatorAction";
import { presentRenderProgress } from "../render/renderProgress";
import { useWorkflowContext } from "./WorkflowWorkspace";

export { stagePresentation } from "./workflowStages";

export default function OverviewPage() {
  const { snapshot, refresh } = useWorkflowContext();
  const workflow = snapshot.workflow;
  const isManualPackage =
    !!workflow.manual_edit_package_url ||
    workflow.production_phase === "manual_edit_package";
  const [confirm, setConfirm] = useState<"cancel" | "regenerate" | null>(null);
  const action = useOperatorAction(refresh);

  async function perform(name: "retry" | "resume" | "cancel" | "regenerate") {
    const capability =
      workflow.capabilities[
        `can_${name === "regenerate" ? "regenerate_narration" : name}`
      ];
    if (!capability?.allowed) return;
    const operation =
      name === "retry"
        ? () => api.repetirWorkflow(workflow.id)
        : name === "resume"
          ? () => api.retomarProducao(workflow.id)
          : name === "cancel"
            ? () => api.cancelarWorkflow(workflow.id)
            : () =>
                api.regenerateNarration(
                  workflow.id,
                  createActionKey("regenerate-narration", workflow.id),
                );
    const result = await action.run(
      operation,
      "Ação aceita pelo Orchestrator.",
    );
    if (result)
      coldArtifactCache.invalidateWorkflow(
        workflow.id,
        capability?.invalidates || [],
      );
    setConfirm(null);
  }

  const renderProgress =
    workflow.render?.status &&
    !["completed", "failed"].includes(workflow.render.status)
      ? presentRenderProgress(workflow.render)
      : null;
  return (
    <div className="workspace-grid">
      <Card title="Progresso da produção" className="span-full">
        <div className="stage-grid">
          {productionStages(
            workflow.production_phase,
            workflow.visual_mode === "storyboard",
            !!workflow.manual_edit_package_url,
          ).map(([key, label], index) => {
            const state = stagePresentation(
              workflow.production_phase,
              workflow.production_status,
              key,
              workflow.visual_mode === "storyboard",
              !!workflow.manual_edit_package_url,
            );
            return (
              <Link
                to={workflowPath(workflow.id, sectionForStage(key))}
                className={`stage-card ${state}`}
                key={key}
                aria-current={
                  key === workflow.production_phase ? "step" : undefined
                }
              >
                <span className="stage-number">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <StatusBadge value={state} />
                <strong>{label}</strong>
                <small>
                  {key === workflow.production_phase
                    ? workflow.current_error_message
                    : ""}
                </small>
              </Link>
            );
          })}
          {!isManualPackage && (
            <div className="stage-card">
              <StatusBadge value={workflow.qa?.status || "not_started"} />
              <strong>
                <Link to={workflowPath(workflow.id, "render")}>
                  Qualidade e revisão
                </Link>
              </strong>
            </div>
          )}
        </div>
        {renderProgress?.kind === "progress" ? (
          <Progress value={renderProgress.value} label={renderProgress.label} />
        ) : (
          renderProgress && <p>{renderProgress.label}</p>
        )}
      </Card>
      {workflow.manual_edit_package_url && (
        <ManualPackagePanel
          url={workflow.manual_edit_package_url}
          status={workflow.production_status}
        />
      )}
      {workflow.visual_mode === "storyboard" && (
        <StoryboardPanel
          workflowId={workflow.id}
          phase={workflow.production_phase}
          onChange={refresh}
        />
      )}
      <Card title="Estado atual">
        <DefinitionList
          items={[
            [
              "Produção",
              <StatusBadge
                value={workflow.production_status || workflow.legacy_status}
              />,
            ],
            ["Fase", statusLabel(workflow.production_phase || "legado")],
            [
              "QA",
              <StatusBadge value={workflow.qa?.status || "não executado"} />,
            ],
            [
              "Revisão humana",
              <StatusBadge value={workflow.human_review.state} />,
            ],
            ["Criada", new Date(workflow.created_at).toLocaleString("pt-BR")],
            [
              "Atualizada",
              new Date(workflow.updated_at).toLocaleString("pt-BR"),
            ],
          ]}
        />
      </Card>
      <Card title="Rastreabilidade">
        <details className="artifact-references">
          <summary>Ver identidades técnicas dos artefatos</summary>
          <DefinitionList
            items={[
              ["Narração SHA", workflow.render ? "ver Render" : "—"],
              [
                "NarrationAlignment",
                snapshot.artifacts.narration_alignment?.fingerprint || "—",
              ],
              ...(workflow.visual_mode === "storyboard"
                ? []
                : ([
                    [
                      "VisualPlan",
                      snapshot.artifacts.visual_plan?.fingerprint || "—",
                    ],
                  ] as [string, string][])),
              [
                "ProductionTimeline",
                snapshot.artifacts.production_timeline?.fingerprint || "—",
              ],
              [
                "PresentationSpec",
                snapshot.artifacts.presentation_fingerprint || "—",
              ],
              [
                "AudioMixPlan",
                snapshot.artifacts.audio_mix_plan_fingerprint || "—",
              ],
              ["Render", workflow.render?.render_fingerprint || "—"],
            ]}
          />
        </details>
      </Card>
      <Card title="Ações permitidas" className="span-full">
        {action.error && (
          <Alert tone={action.conflict ? "warning" : "danger"}>
            {action.conflict ? "Conflito de estado: " : ""}
            {action.error}
          </Alert>
        )}
        {action.notice && <Alert tone="success">{action.notice}</Alert>}
        <div className="action-grid">
          <ActionButton
            capability={workflow.capabilities.can_retry}
            disabled={
              action.submitting || !workflow.capabilities.can_retry?.allowed
            }
            onClick={() => void perform("retry")}
          >
            Repetir fase
          </ActionButton>
          <ActionButton
            capability={workflow.capabilities.can_resume}
            disabled={
              action.submitting || !workflow.capabilities.can_resume?.allowed
            }
            onClick={() => void perform("resume")}
          >
            Retomar produção
          </ActionButton>
          <ActionButton
            capability={workflow.capabilities.can_cancel}
            disabled={
              action.submitting || !workflow.capabilities.can_cancel?.allowed
            }
            onClick={() => setConfirm("cancel")}
          >
            Cancelar
          </ActionButton>
          <ActionButton
            capability={workflow.capabilities.can_regenerate_narration}
            disabled={
              action.submitting ||
              !workflow.capabilities.can_regenerate_narration?.allowed
            }
            onClick={() => setConfirm("regenerate")}
          >
            Regenerar narração
          </ActionButton>
        </div>
        <Diagnostics
          label="Permissões e impactos técnicos"
          value={workflow.capabilities}
        />
      </Card>
      {workflow.current_error_message && (
        <Card title="O que impediu a produção" className="span-full">
          <Alert tone="danger">
            <strong>{workflow.current_error_category || "erro"}:</strong>{" "}
            {workflow.current_error_message}
          </Alert>
        </Card>
      )}
      <ConfirmDialog
        open={confirm === "cancel"}
        title="Cancelar produção"
        consequence="Interrompe novas transições. Artefatos duráveis já produzidos são preservados e a produção poderá ser retomada."
        confirmLabel="Cancelar produção"
        dangerous
        onClose={() => setConfirm(null)}
        onConfirm={() => void perform("cancel")}
      />
      <ConfirmDialog
        open={confirm === "regenerate"}
        title="Regenerar narração completa"
        consequence="Cria novos takes e preserva o histórico. Artefatos dependentes deixam de representar a narração corrente."
        invalidates={
          workflow.capabilities.can_regenerate_narration?.invalidates
        }
        confirmLabel="Regenerar narração"
        dangerous
        onClose={() => setConfirm(null)}
        onConfirm={() => void perform("regenerate")}
      />
    </div>
  );
}
