import { useCallback, useState } from "react";
import { api, ProductionQAReport, WorkflowRender } from "../../api/client";
import { operatorApi, ReviewDecision } from "../../api/operator";
import { ActionButton, Alert, Card, DefinitionList, Diagnostics, EmptyState, LoadingState, Progress, StatusBadge, TableWrap } from "../../components/ui";
import { coldArtifactCache } from "../../data/coldArtifactCache";
import { createActionKey } from "../../data/operatorAction";
import { useColdArtifact } from "../../hooks/useColdArtifact";
import { useOperatorAction } from "../../hooks/useOperatorAction";
import { useWorkflowContext } from "../workflow/WorkflowWorkspace";

export function reviewBanner(state: string, historicalStatus?: string | null) {
  if (state === "stale") return `${(historicalStatus || "REVISÃO").toUpperCase()} HISTÓRICA — RENDER CORRENTE NÃO REVISADO`;
  if (state === "pending") return "RENDER CORRENTE NÃO REVISADO";
  return state.replace(/_/g, " ").toUpperCase();
}

export default function RenderPage() {
  const { snapshot, refresh } = useWorkflowContext();
  const workflow = snapshot.workflow;
  const renderLoader = useCallback((signal?: AbortSignal) => api.obterRender(workflow.id, signal), [workflow.id]);
  const qaLoader = useCallback((signal?: AbortSignal) => api.obterQAReport(workflow.id, "standard", signal), [workflow.id]);
  const renderIdentity = workflow.render ? `${workflow.render.id}:${workflow.render.updated_at}` : null;
  const qaIdentity = workflow.qa ? `${workflow.qa.id}:${workflow.qa.status}:${workflow.qa.warning_count}:${workflow.qa.failed_count}` : null;
  const render = useColdArtifact<WorkflowRender>(workflow.id, "render", renderIdentity, renderLoader);
  const qa = useColdArtifact<ProductionQAReport>(workflow.id, "qa", qaIdentity, qaLoader);
  const [decision, setDecision] = useState<ReviewDecision>("approved");
  const [note, setNote] = useState("");
  const action = useOperatorAction(refresh);

  async function runQA(mode: "standard" | "deep") {
    const response = await action.run(() => api.executarQA(workflow.id, mode, Boolean(workflow.qa)), `QA ${mode} concluído.`);
    if (response) coldArtifactCache.invalidateWorkflow(workflow.id, ["qa"]);
  }

  async function submitReview() {
    const current = render.data;
    if (!current?.render_fingerprint || !current.output?.sha256) return;
    if (["changes_requested", "rejected"].includes(decision) && !note.trim()) return;
    const response = await action.run(() => operatorApi.submitReview(workflow.id, {
      workflow_render_id: current.id,
      render_fingerprint: current.render_fingerprint!,
      output_sha256: current.output!.sha256,
      qa_report_id: qa.data?.id || null,
      qa_request_fingerprint: qa.data?.request_fingerprint || null,
      status: decision,
      note: note.trim() || null,
    }, createActionKey(`human-review-${decision}`, workflow.id)), "Revisão registrada para o render exibido.");
    if (response) setNote("");
  }

  if (render.loading && !render.data) return <LoadingState label="Carregando render…" />;
  return <div className="workspace-stack">
    {(render.error || qa.error) && <Alert tone="danger">{render.error || qa.error}</Alert>}
    <Card title="Render final">
      {!render.data ? <EmptyState title="Render corrente ainda não disponível" /> : <>
        <div className="split-row"><StatusBadge value={render.data.status} /><span>{render.data.phase}</span></div>
        <Progress value={render.data.progress} label={`${render.data.frames_rendered}/${render.data.total_frames ?? "?"} frames`} />
        {render.data.status === "completed" && render.data.output ? <video className="video-player" controls preload="metadata" src={api.urlVideo(workflow.id)} /> : null}
        {render.data.error && <Alert tone="danger"><strong>{render.data.error.category}:</strong> {render.data.error.message}</Alert>}
        <DefinitionList items={[
          ["Render fingerprint", <code>{render.data.render_fingerprint || "—"}</code>],
          ["Manifest fingerprint", <code>{render.data.manifest_fingerprint || "—"}</code>],
          ["Output SHA", <code>{render.data.output?.sha256 || "—"}</code>],
          ["Vídeo", render.data.output ? `${render.data.output.width}×${render.data.output.height} · ${render.data.output.fps} fps · ${render.data.output.video_codec}` : "—"],
          ["Áudio", render.data.output ? `${render.data.output.audio_codec} · ${render.data.output.audio_sample_rate} Hz` : "—"],
          ["Duração / tamanho", render.data.output ? `${render.data.output.duration_seconds.toFixed(2)}s · ${(render.data.output.byte_size / 1024 / 1024).toFixed(1)} MiB` : "—"],
          ["Início / conclusão", `${render.data.started_at ? new Date(render.data.started_at).toLocaleString("pt-BR") : "—"} / ${render.data.completed_at ? new Date(render.data.completed_at).toLocaleString("pt-BR") : "—"}`],
        ]} />
        <Diagnostics label="Render metrics" value={render.data.metrics} />
      </>}
    </Card>
    <Card title="QA automático">
      {!qa.data ? <EmptyState title="QA ainda não executado para o render corrente" /> : <>
        <DefinitionList items={[["Status", <StatusBadge value={qa.data.status} />], ["Publication ready", <StatusBadge value={qa.data.publication_ready ? "ready" : "not ready"} />], ["Modo / policy", `${qa.data.qa_mode} · ${qa.data.policy_name}@${qa.data.policy_version}`], ["Checks", `${qa.data.passed_count} passed · ${qa.data.warning_count} warning · ${qa.data.failed_count} failed · ${qa.data.blocked_count} blocked`], ["Fingerprint", <code>{qa.data.request_fingerprint}</code>]]} />
        <TableWrap><table><thead><tr><th>Check</th><th>Categoria</th><th>Resultado</th><th>Mensagem</th><th>Esperado / atual</th></tr></thead><tbody>{qa.data.checks.map((check) => <tr key={check.code}><td><code>{check.code}</code></td><td>{check.category}</td><td><StatusBadge value={check.result} /></td><td>{check.message}<Diagnostics label="Detalhes" value={check.details} /></td><td><Diagnostics label="Valores" value={{ expected: check.expected, actual: check.actual }} /></td></tr>)}</tbody></table></TableWrap>
      </>}
      <div className="inline-actions"><ActionButton capability={workflow.capabilities.can_run_standard_qa} disabled={action.submitting} onClick={() => void runQA("standard")}>Executar QA standard</ActionButton><ActionButton capability={workflow.capabilities.can_run_deep_qa} disabled={action.submitting} onClick={() => void runQA("deep")}>Executar QA profundo</ActionButton></div>
    </Card>
    <Card title="Human Review">
      <Alert tone={workflow.human_review.state === "approved" ? "success" : workflow.human_review.state === "changes_requested" || workflow.human_review.state === "rejected" ? "danger" : "warning"}><strong>{reviewBanner(workflow.human_review.state, workflow.human_review.latest_historical?.status)}</strong></Alert>
      {workflow.human_review.current && <DefinitionList items={[["Decisão corrente", <StatusBadge value={workflow.human_review.current.status} />], ["Nota", workflow.human_review.current.note || "—"], ["Data", new Date(workflow.human_review.current.created_at).toLocaleString("pt-BR")], ["Render aprovado", <code>{workflow.human_review.current.output_sha256}</code>]]} />}
      {workflow.human_review.state === "stale" && workflow.human_review.latest_historical && <details><summary>Revisão histórica</summary><DefinitionList items={[["Status", workflow.human_review.latest_historical.status], ["Output antigo", <code>{workflow.human_review.latest_historical.output_sha256}</code>], ["Nota", workflow.human_review.latest_historical.note || "—"]]} /></details>}
      <fieldset disabled={action.submitting || workflow.capabilities.can_review?.allowed === false} className="review-form"><legend>Registrar decisão para o render exibido</legend><label>Decisão<select value={decision} onChange={(event) => setDecision(event.target.value as ReviewDecision)}><option value="approved">Aprovar</option><option value="changes_requested">Solicitar alterações</option><option value="rejected">Rejeitar</option></select></label><label>Nota {decision !== "approved" && <strong>(obrigatória)</strong>}<textarea rows={4} maxLength={4000} value={note} onChange={(event) => setNote(event.target.value)} /></label><button disabled={decision !== "approved" && !note.trim()} onClick={() => void submitReview()}>Registrar Human Review</button></fieldset>
      {workflow.capabilities.can_review?.reason && <small className="block-meta">{workflow.capabilities.can_review.reason}</small>}
      {action.error && <Alert tone={action.conflict ? "warning" : "danger"}>{action.conflict ? "O render mudou: " : ""}{action.error}</Alert>}{action.notice && <Alert tone="success">{action.notice}</Alert>}
    </Card>
  </div>;
}
