import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { api, ProductionQAReport, PronunciationReplacement, Workflow, WorkflowRender } from "../api/client";
import { shouldPollQA, shouldPollRender } from "./polling";

function formatPronunciation(item: PronunciationReplacement) {
  const original = item.original_text ?? item.term ?? "?";
  const replacement = item.replacement_text ?? item.replacement ?? "?";
  const identity = item.entry_id ? ` [${item.entry_id}@${item.entry_version ?? "?"}]` : "";
  const count = item.count && item.count > 1 ? ` (${item.count}x)` : "";
  return `${original} → ${replacement}${identity}${count}`;
}

export default function WorkflowDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [render, setRender] = useState<WorkflowRender | null>(null);
  const [qa, setQa] = useState<ProductionQAReport | null>(null);
  const [qaExecutando, setQaExecutando] = useState(false);

  async function carregar() {
    if (!id) return;
    try {
      const wf = await api.obterWorkflow(id);
      setWorkflow(wf);
      if (shouldPollRender(wf)) {
        setRender(await api.obterRender(id).catch(() => null));
      } else {
        setRender(null);
      }
      if (shouldPollQA(wf)) {
        setQa(await api.obterQAReport(id).catch(() => null));
      } else {
        setQa(null);
      }
      setErro(null);
    } catch (e) {
      setErro((e as Error).message);
    }
  }

  useEffect(() => {
    carregar();
    const interval = setInterval(carregar, 3000); // Poll a cada 3s
    return () => clearInterval(interval);
  }, [id]);

  if (erro) return <div style={{ color: "crimson", padding: "2rem" }}>Erro: {erro}</div>;
  if (!workflow) return <div style={{ padding: "2rem" }}>Carregando...</div>;

  async function executarQA(mode: "standard" | "deep", force: boolean) {
    if (!id) return;
    setQaExecutando(true);
    try {
      const result = await api.executarQA(id, mode, force);
      setQa(result.report);
      setErro(null);
    } catch (e) {
      setErro((e as Error).message);
    } finally {
      setQaExecutando(false);
    }
  }

  function actionKey(label: string) {
    const suffix = typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
    return `ideas-hub:${label}:${id}:${suffix}`;
  }

  async function retryBlock(order: number) {
    if (!id) return;
    try { await api.retryAudioBlock(id, order, actionKey("retry-block")); await carregar(); }
    catch (e) { setErro((e as Error).message); }
  }

  async function regenerateBlock(order: number) {
    if (!id || !window.confirm("Gerar um novo take para este bloco? O take atual será preservado no histórico.")) return;
    try { await api.regenerateAudioBlock(id, order, actionKey("regenerate-block")); await carregar(); }
    catch (e) { setErro((e as Error).message); }
  }

  async function regenerateNarration() {
    if (!id || !window.confirm("Regenerar toda a narração? Os takes e artefatos antigos serão preservados.")) return;
    try { await api.regenerateNarration(id, actionKey("regenerate-narration")); await carregar(); }
    catch (e) { setErro((e as Error).message); }
  }

  const statusColors: Record<string, string> = {
    agendada: "#6c757d",
    gerando_audio: "#007bff",
    audio_concluido: "#28a745",
    erro: "#dc3545",
    cancelada: "#ffc107",
  };

  return (
    <div style={{ fontFamily: "sans-serif", maxWidth: 1000, margin: "2rem auto", padding: "0 1rem" }}>
      <button onClick={() => navigate("/")} style={{ marginBottom: "1rem" }}>
        ← Voltar
      </button>

      <h1>Workflow {workflow.id.slice(0, 8)}...</h1>

      <div style={{ marginBottom: "2rem", padding: "1rem", background: "#f5f5f5", borderRadius: "8px" }}>
        <p><strong>Status:</strong> <span style={{ color: statusColors[workflow.status] }}>{workflow.status}</span></p>
        <p><strong>Perfil de Produção:</strong> {workflow.perfil_producao || "—"}</p>
        <p><strong>Retries da execução:</strong> {workflow.tentativas}</p>
        <p><strong>Fase de áudio:</strong> {workflow.audio_phase}</p>
        <p><strong>Produção:</strong> {workflow.production_status || "legado / inativa"}</p>
        <p><strong>Fase de produção:</strong> {workflow.production_phase || "—"}</p>
        <p><strong>Duração Total:</strong> {workflow.duracao_audio_segundos ? `${workflow.duracao_audio_segundos.toFixed(1)}s` : "—"}</p>
        <p><strong>Formato final:</strong> {workflow.audio_final_sample_rate ? `${workflow.audio_final_sample_rate} Hz / ${workflow.audio_final_channels} canal` : "—"}</p>

        {workflow.erro_mensagem && (
          <p style={{ color: "crimson" }}><strong>Erro:</strong> {workflow.erro_mensagem}</p>
        )}
        {workflow.production_error_message && (
          <p style={{ color: "crimson" }}><strong>Erro de produção:</strong> {workflow.production_error_category}: {workflow.production_error_message}</p>
        )}

        {render && (
          <div style={{ marginTop: "1rem" }}>
            <p><strong>Render:</strong> {render.status} / {render.phase} ({Math.round(render.progress * 100)}%)</p>
            {render.total_frames && <p><strong>Frames:</strong> {render.frames_rendered}/{render.total_frames}</p>}
            {render.error && <p style={{ color: "crimson" }}>{render.error.category}: {render.error.message}</p>}
            {render.status === "completed" && (
              <a href={api.urlVideo(workflow.id)} download>Baixar vídeo final</a>
            )}
          </div>
        )}

        {workflow.production_status === "completed" && (
          <div style={{ marginTop: "1rem", borderTop: "1px solid #ddd", paddingTop: "1rem" }}>
            <h3>QA de produção</h3>
            <p><strong>Status:</strong> {qa?.status || "Ainda não executado"}</p>
            <p>
              <strong>Release:</strong>{" "}
              {!qa
                ? "Não avaliado"
                : qa.publication_ready
                  ? qa.status === "passed_with_warnings"
                    ? "Pronto para publicação — com avisos"
                    : "Pronto para publicação"
                  : qa.status === "blocked"
                    ? "QA bloqueado"
                    : "Não aprovado pelo QA"}
            </p>
            {qa && (
              <p>
                <strong>Checks:</strong> {qa.passed_count} aprovados, {qa.warning_count} avisos,
                {" "}{qa.failed_count} falhas, {qa.blocked_count} bloqueados
              </p>
            )}
            {qa && qa.checks.some((item) => item.result === "warning" || item.result === "failed" || item.result === "blocked") && (
              <details>
                <summary>Ver avisos e falhas</summary>
                <ul>
                  {qa.checks
                    .filter((item) => ["warning", "failed", "blocked"].includes(item.result))
                    .map((item) => <li key={item.code}><code>{item.code}</code>: {item.message}</li>)}
                </ul>
              </details>
            )}
            <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
              <button disabled={qaExecutando} onClick={() => executarQA("standard", Boolean(qa))}>
                {qa ? "Reexecutar QA" : "Executar QA"}
              </button>
              <button disabled={qaExecutando} onClick={() => executarQA("deep", true)}>
                Executar QA profundo
              </button>
            </div>
          </div>
        )}

        {workflow.total_blocos !== undefined && (
          <div style={{ marginTop: "1rem" }}>
            <p><strong>Progresso:</strong></p>
            <ul>
              <li>Total de blocos: {workflow.total_blocos}</li>
              <li>Concluídos: {workflow.blocos_concluidos}</li>
              <li>Pendentes: {workflow.blocos_pendentes}</li>
              <li>Gerando: {workflow.blocos_gerando}</li>
              <li>Com erro: {workflow.blocos_erro}</li>
            </ul>
          </div>
        )}

        <div style={{ marginTop: "1rem" }}>
          {workflow.status === "audio_concluido" && workflow.audio_available && (
            <div>
              <p><strong>Áudio Final:</strong></p>
              <audio controls src={api.urlAudio(workflow.id)} style={{ width: "100%", maxWidth: "500px" }} />
            </div>
          )}

          {workflow.status === "erro" && (
            <button onClick={() => api.repetirWorkflow(workflow.id).then(carregar)}>
              Repetir Workflow
            </button>
          )}

          {workflow.production_status === "failed" && workflow.status !== "erro" && (
            <button onClick={() => api.repetirWorkflow(workflow.id).then(carregar)}>
              Repetir fase de produção
            </button>
          )}

          <button onClick={regenerateNarration}>Regenerar narração completa</button>

          {(workflow.production_status === "blocked" || workflow.production_status === null) && (
            <button onClick={() => api.retomarProducao(workflow.id).then(carregar)}>
              Retomar produção
            </button>
          )}

          {(workflow.production_status === "queued" || workflow.production_status === "running") && (
            <button onClick={() => api.cancelarWorkflow(workflow.id).then(carregar)}>
              Cancelar
            </button>
          )}
        </div>
      </div>

      <h2>Blocos de Áudio</h2>
      {workflow.blocos && workflow.blocos.length > 0 ? (
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "2px solid #ccc" }}>
              <th style={{ padding: "8px" }}>#</th>
              <th style={{ padding: "8px" }}>Tipo</th>
              <th style={{ padding: "8px" }}>Status</th>
              <th style={{ padding: "8px" }}>Duração</th>
              <th style={{ padding: "8px" }}>Voz / geração</th>
              <th style={{ padding: "8px" }}>Seed</th>
              <th style={{ padding: "8px" }}>Takes / tentativa atual</th>
              <th style={{ padding: "8px" }}>Texto</th>
              <th style={{ padding: "8px" }}>Erro</th>
              <th style={{ padding: "8px" }}>Áudio</th>
            </tr>
          </thead>
          <tbody>
            {workflow.blocos.map((bloco) => (
              <tr key={bloco.id} style={{ borderBottom: "1px solid #eee" }}>
                <td style={{ padding: "8px" }}>{bloco.ordem + 1}</td>
                <td style={{ padding: "8px" }}>{bloco.tipo}</td>
                <td style={{ padding: "8px" }}>
                  <span style={{ color: statusColors[bloco.status] || "#000" }}>
                    {bloco.status}
                  </span>
                </td>
                <td style={{ padding: "8px" }}>
                  {bloco.duracao_segundos ? `${bloco.duracao_segundos.toFixed(1)}s` : "—"}
                </td>
                <td style={{ padding: "8px" }}>
                  {bloco.voice_profile || "—"} / {bloco.generation_profile || "—"}
                </td>
                <td style={{ padding: "8px" }}>{bloco.resolved_seed ?? "—"}</td>
                <td style={{ padding: "8px" }}>
                  {bloco.generation_attempt + 1} / {bloco.takes?.length || 0}
                  <div style={{ display: "flex", gap: "0.25rem", marginTop: "0.25rem" }}>
                    {(bloco.status === "erro" || bloco.takes?.some((take) => take.status === "failed" && !take.is_current)) && <button onClick={() => retryBlock(bloco.ordem)}>Retry bloco</button>}
                    <button onClick={() => regenerateBlock(bloco.ordem)}>Novo take</button>
                  </div>
                  {bloco.takes?.length > 0 && <details style={{ marginTop: "0.25rem" }}>
                    <summary>Histórico</summary>
                    <ul>
                      {bloco.takes.map((take) => (
                        <li key={take.id}>
                          #{take.attempt_index} {take.kind} — {take.status}, seed {take.resolved_seed ?? take.requested_seed ?? "—"}
                          {take.error_category ? ` — ${take.error_category}: ${take.error_message || ""}` : ""}
                          {take.is_current ? " (atual)" : ""}
                          {take.synthesis_text ? <details>
                            <summary>Proveniência do take</summary>
                            <div>Canônico: {take.canonical_text || "—"}</div>
                            <div>Normalizado: {take.normalized_text || "—"}</div>
                            <div>Síntese: {take.synthesis_text}</div>
                            {take.pronunciation_replacements?.length ? (
                              <div>Pronúncias: {take.pronunciation_replacements.map(formatPronunciation).join(", ")}</div>
                            ) : null}
                          </details> : null}
                        </li>
                      ))}
                    </ul>
                  </details>}
                </td>
                <td style={{ padding: "8px", maxWidth: 260 }}>
                  <details><summary>Ver texto</summary>
                    <p><strong>Canônico:</strong> {bloco.texto}</p>
                    <p><strong>Normalizado:</strong> {bloco.normalized_text || "—"}</p>
                    <p><strong>Síntese:</strong> {bloco.synthesis_text || "—"}</p>
                    {bloco.pronunciation_replacements?.length ? (
                      <p><strong>Pronúncias:</strong> {bloco.pronunciation_replacements.map(formatPronunciation).join(", ")}</p>
                    ) : null}
                  </details>
                </td>
                <td style={{ padding: "8px", color: "crimson" }}>
                  {bloco.erro_mensagem || "—"}
                </td>
                <td style={{ padding: "8px" }}>
                  {bloco.status === "concluido" && bloco.audio_sha256 && (
                    <audio controls src={api.urlAudioBloco(workflow.id, bloco.id)} style={{ height: 30 }} />
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p>Nenhum bloco encontrado.</p>
      )}
    </div>
  );
}
