import { useCallback, useState } from "react";
import { api, ProductionQAReport, WorkflowRender } from "../../api/client";
import { operatorApi, ReviewDecision } from "../../api/operator";
import { ActionButton, Alert, Card, DefinitionList, Diagnostics, EmptyState, LoadingState, Progress, StatusBadge, TableWrap, CopyButton } from "../../components/ui";
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

  const hasVideo = render.data?.status === "completed" && render.data.output;
  const reviewState = workflow.human_review.state;

  return <div className="review-room">
    {/* Left: Video Player Section */}
    <div className="review-player-section">
      {render.error && <Alert tone="danger">{render.error}</Alert>}

      {!render.data ? (
        <Card title="Render corrente">
          <EmptyState title="Render ainda não disponível" detail="O render será exibido aqui quando estiver pronto." />
        </Card>
      ) : (
        <>
          {/* Video Player */}
          <div className="review-player-wrap">
            {hasVideo ? (
              <video className="media-player" controls preload="metadata" src={api.urlVideo(workflow.id)} />
            ) : (
              <div style={{ display: "grid", placeItems: "center", height: "100%", color: "var(--text-muted)" }}>
                <div style={{ textAlign: "center" }}>
                  <StatusBadge value={render.data.status} />
                  <p style={{ marginTop: "0.5rem" }}>{render.data.phase}</p>
                  {render.data.progress < 1 && <Progress value={render.data.progress} label="Renderizando…" />}
                </div>
              </div>
            )}
          </div>

          {/* Player Info Bar */}
          <div className="review-player-info">
            <div>
              <StatusBadge value={render.data.status} />
              <span style={{ marginLeft: "0.5rem" }}>{render.data.phase}</span>
            </div>
            {render.data.output && (
              <div style={{ display: "flex", gap: "1rem", fontSize: "0.8rem", color: "var(--text-muted)" }}>
                <span>{render.data.output.width}×{render.data.output.height}</span>
                <span>{render.data.output.fps} fps</span>
                <span>{render.data.output.duration_seconds.toFixed(1)}s</span>
                <span>{(render.data.output.byte_size / 1024 / 1024).toFixed(1)} MiB</span>
              </div>
            )}
          </div>

          {/* Render Error */}
          {render.data.error && (
            <Alert tone="danger">
              <strong>{render.data.error.category}:</strong> {render.data.error.message}
            </Alert>
          )}

          {/* Render Details (collapsed) */}
          <details>
            <summary>Detalhes técnicos do render</summary>
            <DefinitionList items={[
              ["Render fingerprint", <CopyButton value={render.data.render_fingerprint || "—"} label="fingerprint" />],
              ["Manifest fingerprint", <code>{render.data.manifest_fingerprint || "—"}</code>],
              ["Output SHA", <CopyButton value={render.data.output?.sha256 || "—"} label="SHA" />],
              ["Vídeo", render.data.output ? `${render.data.output.video_codec}` : "—"],
              ["Áudio", render.data.output ? `${render.data.output.audio_codec} · ${render.data.output.audio_sample_rate} Hz` : "—"],
              ["Início", render.data.started_at ? new Date(render.data.started_at).toLocaleString("pt-BR") : "—"],
              ["Conclusão", render.data.completed_at ? new Date(render.data.completed_at).toLocaleString("pt-BR") : "—"],
            ]} />
            <Diagnostics label="Métricas" value={render.data.metrics} />
          </details>
        </>
      )}
    </div>

    {/* Right: Review Panel */}
    <div className="review-panel-section">
      {qa.error && <Alert tone="danger">{qa.error}</Alert>}

      {/* Human Review Status Banner */}
      <div className={`review-banner ${reviewState}`}>
        <div className="review-banner-title">
          {reviewState === "approved" && "✓ Aprovado"}
          {reviewState === "changes_requested" && "⚠ Alterações solicitadas"}
          {reviewState === "rejected" && "✗ Rejeitado"}
          {reviewState === "stale" && "⚡ Aprovação histórica"}
          {reviewState === "pending" && "○ Aguardando revisão"}
        </div>
        <div className="review-banner-meta">
          {reviewState === "stale" && "O render atual não foi revisado"}
          {reviewState === "pending" && "Registre sua decisão abaixo"}
          {reviewState === "approved" && workflow.human_review.current && `Por ${workflow.human_review.current.operator_label || "operador"} em ${new Date(workflow.human_review.current.created_at).toLocaleDateString("pt-BR")}`}
          {(reviewState === "changes_requested" || reviewState === "rejected") && workflow.human_review.current?.note && `Nota: ${workflow.human_review.current.note}`}
        </div>
      </div>

      {/* QA Summary */}
      <Card title="QA automático">
        {!qa.data ? (
          <EmptyState title="QA não executado" detail="Execute o QA para verificar qualidade técnica." />
        ) : (
          <>
            <div style={{ display: "flex", alignItems: "center", gap: "1rem", marginBottom: "0.75rem" }}>
              <StatusBadge value={qa.data.status} />
              <span style={{ fontSize: "0.9rem" }}>
                {qa.data.passed_count} passed · {qa.data.warning_count} warning · {qa.data.failed_count} failed · {qa.data.blocked_count} blocked
              </span>
            </div>
            <details>
              <summary>Ver todos os checks</summary>
              <TableWrap>
                <table>
                  <thead>
                    <tr><th>Check</th><th>Resultado</th><th>Mensagem</th></tr>
                  </thead>
                  <tbody>
                    {qa.data.checks.map((check) => (
                      <tr key={check.code}>
                        <td><code>{check.code}</code></td>
                        <td><StatusBadge value={check.result} /></td>
                        <td>{check.message}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            </details>
          </>
        )}
        <div className="inline-actions" style={{ marginTop: "0.75rem" }}>
          <ActionButton capability={workflow.capabilities.can_run_standard_qa} disabled={action.submitting} onClick={() => void runQA("standard")}>QA standard</ActionButton>
          <ActionButton capability={workflow.capabilities.can_run_deep_qa} disabled={action.submitting} onClick={() => void runQA("deep")}>QA profundo</ActionButton>
        </div>
      </Card>

      {/* Review Actions */}
      <Card title="Registrar decisão">
        {workflow.capabilities.can_review?.reason && (
          <Alert tone="warning">{workflow.capabilities.can_review.reason}</Alert>
        )}
        {action.error && <Alert tone={action.conflict ? "warning" : "danger"}>{action.conflict ? "O render mudou: " : ""}{action.error}</Alert>}
        {action.notice && <Alert tone="success">{action.notice}</Alert>}

        <fieldset disabled={action.submitting || workflow.capabilities.can_review?.allowed === false} className="review-form">
          <div className="review-actions">
            <button
              className={`review-action-btn ${decision === "approved" ? "selected" : ""}`}
              onClick={() => { setDecision("approved"); if (note === "") setNote(""); }}
              type="button"
            >
              <strong>Aprovar</strong>
              <small>Render aprovado para publicação</small>
            </button>
            <button
              className={`review-action-btn warning ${decision === "changes_requested" ? "selected" : ""}`}
              onClick={() => setDecision("changes_requested")}
              type="button"
            >
              <strong>Solicitar alterações</strong>
              <small>Nota obrigatória</small>
            </button>
            <button
              className={`review-action-btn danger ${decision === "rejected" ? "selected" : ""}`}
              onClick={() => setDecision("rejected")}
              type="button"
            >
              <strong>Rejeitar</strong>
              <small>Nota obrigatória</small>
            </button>
          </div>

          <label style={{ marginTop: "1rem" }}>
            Nota {decision !== "approved" && <strong className="text-danger">(obrigatória)</strong>}
            <textarea
              rows={3}
              maxLength={4000}
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder={decision === "approved" ? "Opcional: adicione uma nota…" : "Descreva o motivo…"}
            />
          </label>

          <button
            disabled={decision !== "approved" && !note.trim()}
            onClick={() => void submitReview()}
            style={{ marginTop: "0.75rem" }}
          >
            Registrar revisão
          </button>
        </fieldset>
      </Card>

      {/* Historical Review Info */}
      {workflow.human_review.state === "stale" && workflow.human_review.latest_historical && (
        <details>
          <summary>Revisão histórica anterior</summary>
          <DefinitionList items={[
            ["Status", <StatusBadge value={workflow.human_review.latest_historical.status} />],
            ["Output", <code>{workflow.human_review.latest_historical.output_sha256}</code>],
            ["Nota", workflow.human_review.latest_historical.note || "—"],
          ]} />
        </details>
      )}
    </div>
  </div>;
}
