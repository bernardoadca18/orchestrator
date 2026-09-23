import { StoryboardPanel } from "./StoryboardPanel";
import { useState } from "react";
import { api, BASE_URL } from "../../api/client";
import { Alert, ActionButton, Card, ConfirmDialog, DefinitionList, Diagnostics, Progress, StatusBadge } from "../../components/ui";
import { coldArtifactCache } from "../../data/coldArtifactCache";
import { createActionKey } from "../../data/operatorAction";
import { useOperatorAction } from "../../hooks/useOperatorAction";
import { presentRenderProgress } from "../render/renderProgress";
import { useWorkflowContext } from "./WorkflowWorkspace";

const videoStages = [
  ["audio", "Narração"],
  ["narration_alignment", "Alignment"],
  ["visual_plan", "VisualPlan"],
  ["production_timeline", "Timeline"],
  ["render", "Render"],
] as const;

const storyboardStages = [["audio", "Narração"], ["narration_alignment", "Alignment"], ["storyboard", "Storyboard"], ["storyboard_assets", "Imagens"], ["production_timeline", "Timeline"], ["render", "Render"]] as const;

export function stagePresentation(phase: string | null, status: string | null, stage: string, storyboard = false) {
  const stages = phase === "manual_edit_package" || stage === "manual_edit_package"
    ? [["audio", "Narração"], ["manual_edit_package", "Pacote para edição"]] as const
    : storyboard ? storyboardStages : videoStages;
  const current = stages.findIndex(([key]) => key === phase);
  const index = stages.findIndex(([key]) => key === stage);
  if (status === "completed") return "completed";
  if (index < current) return "completed";
  if (index > current || current < 0) return "not_started";
  if (["failed", "blocked", "cancelled"].includes(status || "")) return status || "not_started";
  return "active";
}

export default function OverviewPage() {
  const { snapshot, refresh } = useWorkflowContext();
  const workflow = snapshot.workflow;
  const [confirm, setConfirm] = useState<"cancel" | "regenerate" | null>(null);
  const action = useOperatorAction(refresh);

  async function perform(name: "retry" | "resume" | "cancel" | "regenerate") {
    const capability = workflow.capabilities[`can_${name === "regenerate" ? "regenerate_narration" : name}`];
    const operation = name === "retry"
      ? () => api.repetirWorkflow(workflow.id)
      : name === "resume"
        ? () => api.retomarProducao(workflow.id)
        : name === "cancel"
          ? () => api.cancelarWorkflow(workflow.id)
          : () => api.regenerateNarration(workflow.id, createActionKey("regenerate-narration", workflow.id));
    const result = await action.run(operation, "Ação aceita pelo Orchestrator.");
    if (result) coldArtifactCache.invalidateWorkflow(workflow.id, capability?.invalidates || []);
    setConfirm(null);
  }

  const renderProgress = workflow.render?.status && !["completed", "failed"].includes(workflow.render.status)
    ? presentRenderProgress(workflow.render)
    : null;
  return <div className="workspace-grid">
    <Card title="Progresso da produção" className="span-full">
      <div className="stage-grid">{(workflow.manual_edit_package_url ? [["audio", "Narração"], ["manual_edit_package", "Pacote para edição"]] as const : workflow.visual_mode === "storyboard" ? storyboardStages : videoStages).map(([key, label]) => {
        const state = stagePresentation(workflow.production_phase, workflow.production_status, key, workflow.visual_mode === "storyboard");
        return <div className={`stage-card ${state}`} key={key}><StatusBadge value={state} /><strong>{label}</strong><small>{key === workflow.production_phase ? workflow.current_error_message : ""}</small></div>;
      })}
      {!workflow.manual_edit_package_url && <div className="stage-card"><StatusBadge value={workflow.qa?.status || "not_started"} /><strong>QA</strong></div>}
      </div>
      {renderProgress?.kind === "progress"
        ? <Progress value={renderProgress.value} label={renderProgress.label} />
        : renderProgress && <p>{renderProgress.label}</p>}
    </Card>
    {workflow.manual_edit_package_url && <Card title="Pacote para edição manual" className="span-full">
      <a href={`${BASE_URL}${workflow.manual_edit_package_url}`}>Consultar manifest e arquivos do pacote</a>
    </Card>}
    {workflow.visual_mode === "storyboard" && <StoryboardPanel workflowId={workflow.id} phase={workflow.production_phase} onChange={refresh} />}
    <Card title="Estado atual">
      <DefinitionList items={[
        ["Produção", <StatusBadge value={workflow.production_status || workflow.legacy_status} />],
        ["Fase", workflow.production_phase || "legado"],
        ["QA", <StatusBadge value={workflow.qa?.status || "não executado"} />],
        ["Human Review", <StatusBadge value={workflow.human_review.state} />],
        ["Criada", new Date(workflow.created_at).toLocaleString("pt-BR")],
        ["Atualizada", new Date(workflow.updated_at).toLocaleString("pt-BR")],
      ]} />
    </Card>
    <Card title="Identidades correntes">
      <DefinitionList items={[
        ["Narração SHA", workflow.render ? "ver Render" : "—"],
        ["NarrationAlignment", snapshot.artifacts.narration_alignment?.fingerprint || "—"],
        ...(workflow.visual_mode === "storyboard" ? [] : [["VisualPlan", snapshot.artifacts.visual_plan?.fingerprint || "—"]] as [string, string][]),
        ["ProductionTimeline", snapshot.artifacts.production_timeline?.fingerprint || "—"],
        ["PresentationSpec", snapshot.artifacts.presentation_fingerprint || "—"],
        ["AudioMixPlan", snapshot.artifacts.audio_mix_plan_fingerprint || "—"],
        ["Render", workflow.render?.render_fingerprint || "—"],
      ]} />
    </Card>
    <Card title="Ações permitidas" className="span-full">
      {action.error && <Alert tone={action.conflict ? "warning" : "danger"}>{action.conflict ? "Conflito de estado: " : ""}{action.error}</Alert>}
      {action.notice && <Alert tone="success">{action.notice}</Alert>}
      <div className="action-grid">
        <ActionButton capability={workflow.capabilities.can_retry} disabled={action.submitting} onClick={() => void perform("retry")}>Repetir fase</ActionButton>
        <ActionButton capability={workflow.capabilities.can_resume} disabled={action.submitting} onClick={() => void perform("resume")}>Retomar produção</ActionButton>
        <ActionButton capability={workflow.capabilities.can_cancel} disabled={action.submitting} onClick={() => setConfirm("cancel")}>Cancelar</ActionButton>
        <ActionButton capability={workflow.capabilities.can_regenerate_narration} disabled={action.submitting} onClick={() => setConfirm("regenerate")}>Regenerar narração</ActionButton>
      </div>
      <Diagnostics label="Capabilities e impactos" value={workflow.capabilities} />
    </Card>
    {workflow.current_error_message && <Card title="Blocker atual" className="span-full"><Alert tone="danger"><strong>{workflow.current_error_category || "erro"}:</strong> {workflow.current_error_message}</Alert></Card>}
    <ConfirmDialog open={confirm === "cancel"} title="Cancelar produção" consequence="Interrompe novas transições. Artefatos duráveis já produzidos são preservados e a produção poderá ser retomada." confirmLabel="Cancelar produção" dangerous onClose={() => setConfirm(null)} onConfirm={() => void perform("cancel")} />
    <ConfirmDialog open={confirm === "regenerate"} title="Regenerar narração completa" consequence="Cria novos takes e preserva o histórico. Artefatos dependentes deixam de representar a narração corrente." invalidates={workflow.capabilities.can_regenerate_narration?.invalidates} confirmLabel="Regenerar narração" dangerous onClose={() => setConfirm(null)} onConfirm={() => void perform("regenerate")} />
  </div>;
}
